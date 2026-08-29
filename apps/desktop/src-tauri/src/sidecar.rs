use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

type Waiters = Arc<Mutex<HashMap<String, mpsc::Sender<AppResult<Value>>>>>;
type JobWaiters = Arc<Mutex<HashMap<String, mpsc::Sender<JobUpdate>>>>;

enum JobUpdate {
    Activity,
    Finished(AppResult<Value>),
}

pub struct EngineBroker {
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    waiters: Waiters,
    jobs: JobWaiters,
    parent_job: Mutex<Option<ParentJob>>,
}

impl EngineBroker {
    pub fn start(app: &AppHandle, data_dir: &std::path::Path) -> AppResult<Self> {
        let mut command = engine_command(app, data_dir)?;
        #[cfg(windows)]
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        #[cfg(debug_assertions)]
        command.stderr(Stdio::piped());
        #[cfg(not(debug_assertions))]
        command.stderr(Stdio::null());
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()?;
        let parent_job = attach_kill_on_parent(&child)?;
        let input = child.stdin.take().ok_or(AppError::EngineUnavailable)?;
        let output = child.stdout.take().ok_or(AppError::EngineUnavailable)?;
        #[cfg(debug_assertions)]
        if let Some(stderr) = child.stderr.take() {
            if let Err(error) = thread::Builder::new()
                .name("soatvan-engine-stderr".into())
                .spawn(move || {
                    for line in BufReader::new(stderr).lines() {
                        match line {
                            Ok(line) => eprintln!("[soatvan-sidecar] {line}"),
                            Err(error) => {
                                eprintln!("[soatvan-sidecar] stderr read failed: {error}");
                                break;
                            }
                        }
                    }
                })
            {
                let _ = child.kill();
                return Err(error.into());
            }
        }
        let waiters: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let jobs: JobWaiters = Arc::new(Mutex::new(HashMap::new()));
        let reader_waiters = Arc::clone(&waiters);
        let reader_jobs = Arc::clone(&jobs);
        let app_handle = app.clone();
        thread::Builder::new()
            .name("soatvan-engine-reader".into())
            .spawn(move || {
                for line in BufReader::new(output).lines() {
                    let Ok(line) = line else { break };
                    let Ok(frame) = serde_json::from_str::<Value>(&line) else {
                        continue;
                    };
                    if let Some(event) = frame.get("event").and_then(Value::as_str) {
                        let payload = frame.get("data").cloned().unwrap_or(Value::Null);
                        if event == "job.progress" {
                            if let Some(job_id) = payload.get("job_id").and_then(Value::as_str) {
                                if let Some(sender) =
                                    reader_jobs.lock().expect("jobs poisoned").get(job_id)
                                {
                                    let _ = sender.send(JobUpdate::Activity);
                                }
                            }
                        }
                        if matches!(event, "job.completed" | "job.no_findings" | "job.failed") {
                            if let Some(job_id) = payload.get("job_id").and_then(Value::as_str) {
                                if let Some(sender) =
                                    reader_jobs.lock().expect("jobs poisoned").remove(job_id)
                                {
                                    let result = if event == "job.failed" {
                                        Err(AppError::Engine(
                                            payload
                                                .get("code")
                                                .and_then(Value::as_str)
                                                .unwrap_or("ENGINE_PROTOCOL_ERROR")
                                                .to_owned(),
                                        ))
                                    } else {
                                        Ok(payload.clone())
                                    };
                                    let _ = sender.send(JobUpdate::Finished(result));
                                }
                            }
                        }
                        // Sidecar protocol methods use dotted names, while Tauri 2 event
                        // names only allow alphanumeric characters plus - / : _. Keep the
                        // protocol stable and translate only at the WebView bridge.
                        let bridge_event = event.replace('.', "-");
                        let _ = app_handle.emit(&bridge_event, payload);
                        continue;
                    }
                    if let Some(id) = frame.get("id").and_then(Value::as_str) {
                        if let Some(sender) =
                            reader_waiters.lock().expect("waiters poisoned").remove(id)
                        {
                            let result = if frame.get("ok") == Some(&Value::Bool(true)) {
                                Ok(frame.get("result").cloned().unwrap_or(Value::Null))
                            } else {
                                let code = frame
                                    .pointer("/error/code")
                                    .and_then(Value::as_str)
                                    .unwrap_or("ENGINE_PROTOCOL_ERROR");
                                Err(AppError::Engine(code.to_owned()))
                            };
                            let _ = sender.send(result);
                        }
                    }
                }
                fail_pending(&reader_waiters, &reader_jobs);
            })?;
        let broker = Self {
            child: Mutex::new(child),
            input: Mutex::new(input),
            waiters,
            jobs,
            parent_job: Mutex::new(Some(parent_job)),
        };
        let hello = broker.call("engine.hello", json!({}), Duration::from_secs(30))?;
        if hello.get("protocol") != Some(&Value::from(1)) {
            return Err(AppError::EngineProtocol);
        }
        Ok(broker)
    }

    pub fn call(&self, method: &str, params: Value, timeout: Duration) -> AppResult<Value> {
        let id = Uuid::new_v4().to_string();
        let frame = json!({"v": 1, "id": id, "method": method, "params": params});
        let (sender, receiver) = mpsc::channel();
        self.waiters
            .lock()
            .expect("waiters poisoned")
            .insert(id.clone(), sender);
        let write_result = {
            let mut input = self.input.lock().expect("engine input poisoned");
            writeln!(input, "{}", frame).and_then(|_| input.flush())
        };
        if let Err(error) = write_result {
            self.waiters.lock().expect("waiters poisoned").remove(&id);
            return Err(error.into());
        }
        match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(_) => {
                self.waiters.lock().expect("waiters poisoned").remove(&id);
                Err(AppError::EngineTimeout)
            }
        }
    }

    pub fn stop(&self) {
        let _ = self.child.lock().expect("child poisoned").kill();
        if let Some(job) = self.parent_job.lock().expect("parent job poisoned").take() {
            close_parent_job(job);
        }
    }

    pub fn run_job(&self, params: Value, idle_timeout: Duration) -> AppResult<Value> {
        let job_id = params
            .get("job_id")
            .and_then(Value::as_str)
            .ok_or(AppError::EngineProtocol)?
            .to_owned();
        let (sender, receiver) = mpsc::channel();
        self.jobs
            .lock()
            .expect("jobs poisoned")
            .insert(job_id.clone(), sender);
        if let Err(error) = self.call("job.start", params, Duration::from_secs(5)) {
            self.jobs.lock().expect("jobs poisoned").remove(&job_id);
            return Err(error);
        }
        loop {
            match receiver.recv_timeout(idle_timeout) {
                Ok(JobUpdate::Activity) => continue,
                Ok(JobUpdate::Finished(result)) => return result,
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    self.jobs.lock().expect("jobs poisoned").remove(&job_id);
                    let _ = self.call(
                        "job.cancel",
                        json!({"job_id": job_id}),
                        Duration::from_secs(3),
                    );
                    return Err(AppError::EngineTimeout);
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err(AppError::EngineUnavailable);
                }
            }
        }
    }
}

fn fail_pending(waiters: &Waiters, jobs: &JobWaiters) {
    let mut pending = waiters.lock().expect("waiters poisoned");
    for (_, sender) in pending.drain() {
        let _ = sender.send(Err(AppError::EngineUnavailable));
    }
    drop(pending);
    let mut pending_jobs = jobs.lock().expect("jobs poisoned");
    for (_, sender) in pending_jobs.drain() {
        let _ = sender.send(JobUpdate::Finished(Err(AppError::EngineUnavailable)));
    }
}

fn engine_command(app: &AppHandle, data_dir: &std::path::Path) -> AppResult<Command> {
    if cfg!(debug_assertions) {
        let engine = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
        let mut command = Command::new("uv");
        command.args(["run", "--project"]).arg(engine).args([
            "python",
            "-u",
            "-m",
            "soatvan.entrypoints.sidecar",
        ]);
        command.env("SOATVAN_DATA_DIR", data_dir);
        command.env("SOATVAN_DEV_LOG", "1");
        command.env("PYTHONUNBUFFERED", "1");
        command.env("PYTHONIOENCODING", "utf-8");
        if let Some(public_key) = option_env!("SOATVAN_MODEL_PUBLIC_KEY") {
            command.env("SOATVAN_MODEL_PUBLIC_KEY", public_key);
        }
        return Ok(command);
    }
    let executable = if cfg!(windows) {
        "soatvan-engine.exe"
    } else {
        "soatvan-engine"
    };
    let candidate = app
        .path()
        .resource_dir()
        .map(|dir| dir.join("engine").join(executable))
        .ok();
    let path = candidate
        .filter(|p| p.exists())
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|dir| dir.join("engine").join(executable)))
                .filter(|p| p.exists())
        })
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|p| {
                    p.parent()
                        .map(|dir| dir.join("resources").join("engine").join(executable))
                })
                .filter(|p| p.exists())
        })
        .ok_or(AppError::EngineUnavailable)?;

    let mut command = Command::new(path);
    command.env("SOATVAN_DATA_DIR", data_dir);
    command.env("PYTHONUNBUFFERED", "1");
    command.env("PYTHONIOENCODING", "utf-8");
    if let Some(public_key) = option_env!("SOATVAN_MODEL_PUBLIC_KEY") {
        command.env("SOATVAN_MODEL_PUBLIC_KEY", public_key);
    }
    Ok(command)
}

#[cfg(not(windows))]
struct ParentJob;
#[cfg(windows)]
type ParentJob = usize;

#[cfg(not(windows))]
fn attach_kill_on_parent(_: &Child) -> AppResult<ParentJob> {
    Ok(ParentJob)
}

#[cfg(windows)]
fn attach_kill_on_parent(child: &Child) -> AppResult<ParentJob> {
    use std::mem::size_of;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Ok(0);
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            CloseHandle(job);
            return Ok(0);
        }
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, child.id());
        if process.is_null() {
            CloseHandle(job);
            return Ok(0);
        }
        if AssignProcessToJobObject(job, process) == 0 {
            CloseHandle(process);
            CloseHandle(job);
            return Ok(0);
        }
        CloseHandle(process);
        Ok(job as usize)
    }
}

#[cfg(not(windows))]
fn close_parent_job(_: ParentJob) {}

#[cfg(windows)]
fn close_parent_job(job: ParentJob) {
    if job != 0 {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(job as *mut core::ffi::c_void) };
    }
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use std::time::Instant;

    #[test]
    fn closing_job_object_terminates_engine_process() {
        let mut child = Command::new("cmd")
            .args(["/C", "ping 127.0.0.1 -n 30 >nul"])
            .spawn()
            .expect("spawn child");
        let job = attach_kill_on_parent(&child).expect("attach job");
        close_parent_job(job);
        let deadline = Instant::now() + Duration::from_secs(5);
        let exited = loop {
            if child.try_wait().expect("wait child").is_some() {
                break true;
            }
            if Instant::now() >= deadline {
                break false;
            }
            thread::sleep(Duration::from_millis(25));
        };
        if !exited {
            let _ = child.kill();
        }
        child.wait().expect("reap child");
        assert!(
            exited,
            "child survived closing the kill-on-close Job Object"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_eof_releases_request_and_job_waiters() {
        let waiters: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let jobs: JobWaiters = Arc::new(Mutex::new(HashMap::new()));
        let (request_sender, request_receiver) = mpsc::channel();
        let (job_sender, job_receiver) = mpsc::channel();
        waiters
            .lock()
            .unwrap()
            .insert("request".into(), request_sender);
        jobs.lock().unwrap().insert("job".into(), job_sender);
        fail_pending(&waiters, &jobs);
        assert!(matches!(
            request_receiver.recv().unwrap(),
            Err(AppError::EngineUnavailable)
        ));
        assert!(matches!(
            job_receiver.recv().unwrap(),
            JobUpdate::Finished(Err(AppError::EngineUnavailable))
        ));
        assert!(waiters.lock().unwrap().is_empty());
        assert!(jobs.lock().unwrap().is_empty());
    }

    #[test]
    fn tauri_bridge_event_names_are_legal() {
        assert_eq!("job.progress".replace('.', "-"), "job-progress");
        assert_eq!("job.completed".replace('.', "-"), "job-completed");
        assert_eq!("job.no_findings".replace('.', "-"), "job-no_findings");
    }
}
