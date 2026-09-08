use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
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
    app: AppHandle,
    data_dir: PathBuf,
    /// Serialises a respawn against `stop()` and against other respawn callers.
    restart_lock: Mutex<()>,
    /// Set once at shutdown so a concurrent `call()` can never resurrect the
    /// engine after `stop()` has torn it down.
    stopped: AtomicBool,
}

impl EngineBroker {
    pub fn start(app: &AppHandle, data_dir: &Path) -> AppResult<Self> {
        let waiters: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let jobs: JobWaiters = Arc::new(Mutex::new(HashMap::new()));
        let (child, input, parent_job) = spawn_engine(app, data_dir, &waiters, &jobs)?;
        let broker = Self {
            child: Mutex::new(child),
            input: Mutex::new(input),
            waiters,
            jobs,
            parent_job: Mutex::new(Some(parent_job)),
            app: app.clone(),
            data_dir: data_dir.to_path_buf(),
            restart_lock: Mutex::new(()),
            stopped: AtomicBool::new(false),
        };
        broker.handshake()?;
        Ok(broker)
    }

    fn handshake(&self) -> AppResult<()> {
        let hello = self.call_raw("engine.hello", json!({}), Duration::from_secs(30))?;
        if hello.get("protocol") != Some(&Value::from(1)) {
            return Err(AppError::EngineProtocol);
        }
        Ok(())
    }

    /// If the engine process has exited, spawn a fresh one and re-wire the reader
    /// so later calls succeed. A no-op while the engine is healthy. The native
    /// llama.cpp runtime can abort the whole sidecar on a failed allocation or an
    /// unsupported CPU instruction; without this every subsequent model import
    /// (and every review) would fail until the app was restarted by hand.
    fn ensure_alive(&self) -> AppResult<()> {
        if self.stopped.load(Ordering::Acquire) {
            return Err(AppError::EngineUnavailable);
        }
        let _restart = self.restart_lock.lock().expect("restart lock poisoned");
        if self.stopped.load(Ordering::Acquire) {
            return Err(AppError::EngineUnavailable);
        }
        {
            let mut child = self.child.lock().expect("child poisoned");
            match child.try_wait() {
                Ok(None) => return Ok(()), // still running — another caller won the race, or a false alarm
                Ok(Some(status)) => {
                    log::warn!(target: "host", "engine process exited ({status}); restarting");
                }
                Err(error) => {
                    log::warn!(target: "host", "engine process wait failed ({error}); restarting");
                }
            }
        }
        // Make sure nothing is still blocked on the dead process before we swap.
        fail_pending(&self.waiters, &self.jobs);
        let (child, input, parent_job) =
            spawn_engine(&self.app, &self.data_dir, &self.waiters, &self.jobs)?;
        *self.child.lock().expect("child poisoned") = child;
        *self.input.lock().expect("engine input poisoned") = input;
        if let Some(old) = self
            .parent_job
            .lock()
            .expect("parent job poisoned")
            .replace(parent_job)
        {
            close_parent_job(old);
        }
        self.handshake()?;
        log::info!(target: "host", "engine restarted");
        Ok(())
    }

    pub fn call(&self, method: &str, params: Value, timeout: Duration) -> AppResult<Value> {
        match self.call_raw(method, params.clone(), timeout) {
            // A broken pipe (write failed) or an EngineUnavailable handed back by
            // the reader thread on EOF both mean the process died. Bring it back
            // and give the call one more chance. A timeout is left alone — the
            // engine may just be busy loading a large model.
            Err(AppError::EngineUnavailable | AppError::Io(_))
                if !self.stopped.load(Ordering::Acquire) =>
            {
                self.ensure_alive()?;
                self.call_raw(method, params, timeout)
            }
            other => other,
        }
    }

    fn call_raw(&self, method: &str, params: Value, timeout: Duration) -> AppResult<Value> {
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
        // Set first so an in-flight `call()` retry cannot spawn a replacement
        // after we tear down; the restart lock then serialises us against any
        // `ensure_alive()` already past that check.
        self.stopped.store(true, Ordering::Release);
        let _restart = self.restart_lock.lock().expect("restart lock poisoned");
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

/// Spawn the engine process and wire its stdout/stderr to the shared waiter maps.
/// Used both for the first start and for every respawn, so the reader thread the
/// caller ends up with always drains `waiters`/`jobs` on EOF.
fn spawn_engine(
    app: &AppHandle,
    data_dir: &Path,
    waiters: &Waiters,
    jobs: &JobWaiters,
) -> AppResult<(Child, ChildStdin, ParentJob)> {
    let mut command = engine_command(app, data_dir)?;
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW

    // The engine writes diagnostics and Python tracebacks to stderr. Capture
    // them in release too so a user's bug report has something to read; the
    // draining thread below keeps the pipe from filling and stalling a job.
    command.stderr(Stdio::piped());
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()?;
    let parent_job = attach_kill_on_parent(&child)?;
    let input = child.stdin.take().ok_or(AppError::EngineUnavailable)?;
    let output = child.stdout.take().ok_or(AppError::EngineUnavailable)?;
    if let Some(stderr) = child.stderr.take() {
        if let Err(error) = thread::Builder::new()
            .name("soatvan-engine-stderr".into())
            .spawn(move || forward_engine_stderr(stderr))
        {
            let _ = child.kill();
            return Err(error.into());
        }
    }
    if let Err(error) = spawn_reader(output, Arc::clone(waiters), Arc::clone(jobs), app.clone()) {
        let _ = child.kill();
        return Err(error);
    }
    Ok((child, input, parent_job))
}

fn spawn_reader(
    output: ChildStdout,
    reader_waiters: Waiters,
    reader_jobs: JobWaiters,
    app_handle: AppHandle,
) -> AppResult<()> {
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
    Ok(())
}

/// Drain the engine's stderr into the shared log file. Reads raw bytes and
/// decodes lossily so a stray non-UTF-8 byte from a native dependency (llama.cpp)
/// never stops the drain and stalls the child on a full pipe.
fn forward_engine_stderr(stderr: impl Read) {
    let mut reader = BufReader::new(stderr);
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        match reader.read_until(b'\n', &mut buffer) {
            Ok(0) => break,
            Ok(_) => {
                let line = String::from_utf8_lossy(&buffer);
                let line = line.trim_end_matches(['\r', '\n']);
                if line.is_empty() {
                    continue;
                }
                match classify_engine_line(line) {
                    log::Level::Error => log::error!(target: "engine", "{line}"),
                    log::Level::Warn => log::warn!(target: "engine", "{line}"),
                    _ => log::info!(target: "engine", "{line}"),
                }
            }
            Err(error) => {
                log::warn!(target: "engine", "stderr stream closed: {error}");
                break;
            }
        }
    }
}

/// Map an engine stderr line to a log level. The Python side prefixes lines with
/// the `logging` level name; a traceback body has no prefix so match it directly.
fn classify_engine_line(line: &str) -> log::Level {
    if line.contains("ERROR") || line.contains("CRITICAL") || line.contains("Traceback") {
        log::Level::Error
    } else if line.contains("WARNING") {
        log::Level::Warn
    } else {
        log::Level::Info
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
            "--extra",
            "model",
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
    fn engine_stderr_lines_map_to_log_levels() {
        assert!(matches!(
            classify_engine_line("ERROR [soatvan.sidecar] [engine-error] job x failed"),
            log::Level::Error
        ));
        assert!(matches!(
            classify_engine_line("  File \"sidecar.py\", line 1, in main\nTraceback"),
            log::Level::Error
        ));
        assert!(matches!(
            classify_engine_line("WARNING [soatvan.sidecar] Seq2Seq skipped"),
            log::Level::Warn
        ));
        assert!(matches!(
            classify_engine_line("INFO [soatvan.sidecar] job job-1 starting"),
            log::Level::Info
        ));
    }

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
