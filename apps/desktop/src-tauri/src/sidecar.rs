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

type Waiters = Arc<Mutex<HashMap<String, mpsc::Sender<AppResult<Value>>>>>;
type JobWaiters = Arc<Mutex<HashMap<String, mpsc::Sender<AppResult<Value>>>>>;

pub struct EngineBroker {
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    waiters: Waiters,
    jobs: JobWaiters,
    parent_job: Mutex<Option<ParentJob>>,
}

impl EngineBroker {
    pub fn start(app: &AppHandle) -> AppResult<Self> {
        let mut command = engine_command(app)?;
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()?;
        let parent_job = attach_kill_on_parent(&child)?;
        let input = child.stdin.take().ok_or(AppError::EngineUnavailable)?;
        let output = child.stdout.take().ok_or(AppError::EngineUnavailable)?;
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
                                    let _ = sender.send(result);
                                }
                            }
                        }
                        let _ = app_handle.emit(event, payload);
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
                let mut pending = reader_waiters.lock().expect("waiters poisoned");
                for (_, sender) in pending.drain() {
                    let _ = sender.send(Err(AppError::EngineUnavailable));
                }
            })?;
        let broker = Self {
            child: Mutex::new(child),
            input: Mutex::new(input),
            waiters,
            jobs,
            parent_job: Mutex::new(Some(parent_job)),
        };
        let hello = broker.call("engine.hello", json!({}), Duration::from_secs(5))?;
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
        let write_result = writeln!(
            self.input.lock().expect("engine input poisoned"),
            "{}",
            frame
        )
        .and_then(|_| self.input.lock().expect("engine input poisoned").flush());
        if let Err(error) = write_result {
            self.waiters.lock().expect("waiters poisoned").remove(&id);
            return Err(error.into());
        }
        receiver
            .recv_timeout(timeout)
            .map_err(|_| AppError::EngineTimeout)?
    }

    pub fn stop(&self) {
        let _ = self.child.lock().expect("child poisoned").kill();
        if let Some(job) = self.parent_job.lock().expect("parent job poisoned").take() {
            close_parent_job(job);
        }
    }

    pub fn run_job(&self, params: Value, timeout: Duration) -> AppResult<Value> {
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
        receiver.recv_timeout(timeout).map_err(|_| {
            self.jobs.lock().expect("jobs poisoned").remove(&job_id);
            AppError::EngineTimeout
        })?
    }
}

fn engine_command(app: &AppHandle) -> AppResult<Command> {
    if cfg!(debug_assertions) {
        let engine = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
        let mut command = Command::new("uv");
        command.args(["run", "--project"]).arg(engine).args([
            "python",
            "-m",
            "soatvan.entrypoints.sidecar",
        ]);
        return Ok(command);
    }
    let executable = if cfg!(windows) {
        "soatvan-engine.exe"
    } else {
        "soatvan-engine"
    };
    let path = app
        .path()
        .resource_dir()
        .map_err(|_| AppError::EngineUnavailable)?
        .join("engine")
        .join(executable);
    Ok(Command::new(path))
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
            return Err(AppError::EngineUnavailable);
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
            return Err(AppError::EngineUnavailable);
        }
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, child.id());
        if process.is_null() || AssignProcessToJobObject(job, process) == 0 {
            if !process.is_null() {
                CloseHandle(process);
            }
            CloseHandle(job);
            return Err(AppError::EngineUnavailable);
        }
        CloseHandle(process);
        Ok(job as usize)
    }
}

#[cfg(not(windows))]
fn close_parent_job(_: ParentJob) {}

#[cfg(windows)]
fn close_parent_job(job: ParentJob) {
    unsafe { windows_sys::Win32::Foundation::CloseHandle(job as *mut core::ffi::c_void) };
}
