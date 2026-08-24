mod error;
mod model;
mod sidecar;

use crate::{
    error::{AppError, AppResult},
    model::{ModelProvisioner, ModelStatus},
    sidecar::EngineBroker,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Digest;
use std::{
    fs,
    fs::OpenOptions,
    future::Future,
    io::{Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

struct AppState {
    engine: EngineBroker,
    model: Mutex<ModelProvisioner>,
    model_cancel: AtomicBool,
}

#[derive(Debug, Serialize)]
struct DocumentInfo {
    path: String,
    name: String,
    size: u64,
    paragraph_count: u64,
    table_cell_count: u64,
    character_count: u64,
    word_count: u64,
    page_count: Option<u64>,
}

#[derive(Debug, Serialize)]
struct JobResult {
    job_id: String,
    status: String,
    output_path: Option<String>,
    finding_count: u64,
    counts: Value,
}

#[derive(Debug, Serialize, Deserialize)]
struct RuleOptions {
    technical: bool,
    repeated_words: bool,
    confusions: bool,
    syllables: bool,
    administrative_capitalization: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StartJobRequest {
    job_id: String,
    source_path: String,
    preset: String,
    custom_prompt: String,
    use_model: bool,
    rule_options: RuleOptions,
    ignored_words: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct DictionaryEntry {
    word: String,
    note: String,
}

#[tauri::command]
async fn choose_document(
    app: AppHandle,
    state: State<'_, AppState>,
) -> AppResult<Option<DocumentInfo>> {
    let selected = app
        .dialog()
        .file()
        .add_filter("Tệp Word", &["docx"])
        .blocking_pick_file();
    let Some(path) = selected.and_then(|value| value.into_path().ok()) else {
        return Ok(None);
    };
    inspect_impl(&path, &state.engine).map(Some)
}

#[tauri::command]
async fn inspect_document(path: String, state: State<'_, AppState>) -> AppResult<DocumentInfo> {
    inspect_impl(Path::new(&path), &state.engine)
}

fn inspect_impl(path: &Path, engine: &EngineBroker) -> AppResult<DocumentInfo> {
    let canonical = validate_docx(path)?;
    let result = engine.call(
        "document.inspect",
        json!({"source_path": canonical}),
        Duration::from_secs(15),
    )?;
    Ok(DocumentInfo {
        path: canonical.to_string_lossy().into_owned(),
        name: result["name"].as_str().unwrap_or_default().to_owned(),
        size: result["size"].as_u64().unwrap_or(0),
        paragraph_count: result["paragraph_count"].as_u64().unwrap_or(0),
        table_cell_count: result["table_cell_count"].as_u64().unwrap_or(0),
        character_count: result["character_count"].as_u64().unwrap_or(0),
        word_count: result["word_count"].as_u64().unwrap_or(0),
        page_count: result["page_count"].as_u64(),
    })
}

#[tauri::command]
async fn start_job(request: StartJobRequest, state: State<'_, AppState>) -> AppResult<JobResult> {
    let StartJobRequest {
        job_id,
        source_path,
        preset,
        custom_prompt,
        use_model,
        rule_options,
        ignored_words,
    } = request;
    let source = validate_docx(Path::new(&source_path))?;
    if !matches!(preset.as_str(), "standard" | "administrative" | "spelling") {
        return Err(AppError::Engine("PRESET_INVALID".into()));
    }
    if custom_prompt.chars().count() > 1000 {
        return Err(AppError::Engine("CUSTOM_PROMPT_TOO_LONG".into()));
    }
    if !custom_prompt.trim().is_empty() && !use_model {
        return Err(AppError::Engine("CUSTOM_PROMPT_REQUIRES_MODEL".into()));
    }
    if use_model && engine_model_status(&state.engine, true)?.state != "ready" {
        return Err(AppError::Engine("MODEL_CLASSIFIER_NOT_READY".into()));
    }
    if ignored_words.len() > 500
        || ignored_words.iter().any(|word| {
            word.trim().is_empty()
                || word.chars().count() > 120
                || word
                    .chars()
                    .any(|character| matches!(character, '\r' | '\n' | '\0'))
        })
    {
        return Err(AppError::Engine("SESSION_DICTIONARY_INVALID".into()));
    }
    let workspace = tempfile::tempdir()?;
    let controlled_source = workspace.path().join("source.docx");
    fs::copy(&source, &controlled_source)?;
    let parent = source.parent().ok_or(AppError::InvalidPath)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".soatvan-")
        .suffix(".docx.tmp")
        .tempfile_in(parent)?;
    temporary.flush()?;
    let temporary_path = temporary.path().to_path_buf();
    drop(temporary);
    let response = state.engine.run_job(
        json!({
            "job_id": job_id, "source_path": controlled_source,
            "temporary_output_path": temporary_path, "preset": preset,
            "custom_prompt": custom_prompt, "use_model": use_model,
            "rule_config": rule_options, "ignored_words": ignored_words
        }),
        Duration::from_secs(10 * 60),
    );
    let response = match response {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_file(&temporary_path);
            return Err(error);
        }
    };
    let finding_count = response["finding_count"].as_u64().unwrap_or(0);
    if finding_count == 0 {
        let _ = fs::remove_file(&temporary_path);
        return Ok(JobResult {
            job_id,
            status: "no_findings".into(),
            output_path: None,
            finding_count: 0,
            counts: json!({}),
        });
    }
    let output = finalize_output_or_cleanup(&source, &temporary_path)?;
    Ok(JobResult {
        job_id,
        status: "completed".into(),
        output_path: Some(output.to_string_lossy().into_owned()),
        finding_count,
        counts: response["counts"].clone(),
    })
}

#[tauri::command]
fn cancel_job(job_id: String, state: State<'_, AppState>) -> AppResult<bool> {
    let result = state.engine.call(
        "job.cancel",
        json!({"job_id": job_id}),
        Duration::from_secs(3),
    )?;
    Ok(result["cancelled"].as_bool().unwrap_or(false))
}

#[tauri::command]
fn open_output(app: AppHandle, path: String, reveal: bool) -> AppResult<()> {
    let canonical = fs::canonicalize(path).map_err(|_| AppError::InvalidPath)?;
    if reveal {
        app.opener()
            .reveal_item_in_dir(canonical)
            .map_err(|_| AppError::InvalidPath)?;
    } else {
        app.opener()
            .open_path(canonical.to_string_lossy(), None::<&str>)
            .map_err(|_| AppError::InvalidPath)?;
    }
    Ok(())
}

#[tauri::command]
fn dictionary_list(query: String, state: State<'_, AppState>) -> AppResult<Vec<DictionaryEntry>> {
    let value = state.engine.call(
        "dictionary.list",
        json!({"query": query}),
        Duration::from_secs(5),
    )?;
    Ok(serde_json::from_value(value["entries"].clone())?)
}
#[tauri::command]
fn dictionary_upsert(
    word: String,
    note: String,
    state: State<'_, AppState>,
) -> AppResult<DictionaryEntry> {
    Ok(serde_json::from_value(state.engine.call(
        "dictionary.upsert",
        json!({"word":word,"note":note}),
        Duration::from_secs(5),
    )?)?)
}
#[tauri::command]
fn dictionary_delete(word: String, state: State<'_, AppState>) -> AppResult<bool> {
    Ok(state.engine.call(
        "dictionary.delete",
        json!({"word":word}),
        Duration::from_secs(5),
    )?["deleted"]
        .as_bool()
        .unwrap_or(false))
}
#[tauri::command]
fn dictionary_import(app: AppHandle, state: State<'_, AppState>) -> AppResult<Option<u64>> {
    let selected = app
        .dialog()
        .file()
        .add_filter("CSV UTF-8", &["csv"])
        .blocking_pick_file();
    let Some(path) = selected.and_then(|value| value.into_path().ok()) else {
        return Ok(None);
    };
    Ok(state.engine.call(
        "dictionary.import",
        json!({"path":path}),
        Duration::from_secs(30),
    )?["count"]
        .as_u64())
}
#[tauri::command]
fn dictionary_export(app: AppHandle, state: State<'_, AppState>) -> AppResult<Option<u64>> {
    let selected = app
        .dialog()
        .file()
        .add_filter("CSV UTF-8", &["csv"])
        .set_file_name("tu-dien-soatvan.csv")
        .blocking_save_file();
    let Some(path) = selected.and_then(|value| value.into_path().ok()) else {
        return Ok(None);
    };
    Ok(state.engine.call(
        "dictionary.export",
        json!({"path":path}),
        Duration::from_secs(30),
    )?["count"]
        .as_u64())
}

#[tauri::command]
fn model_status(activate: bool, state: State<'_, AppState>) -> AppResult<ModelStatus> {
    let (host, pending) = {
        let provisioner = state.model.lock().expect("model poisoned");
        (provisioner.status(), provisioner.has_pending_activation())
    };
    if host.state == "not_installed" {
        if pending {
            deactivate_model(&state.engine)?;
            state
                .model
                .lock()
                .expect("model poisoned")
                .rollback_activation()?;
            return engine_model_status(&state.engine, activate);
        }
        return Ok(host);
    }
    let status = engine_model_status(&state.engine, activate || pending)?;
    if !pending {
        return Ok(status);
    }
    if status.state == "ready" {
        state
            .model
            .lock()
            .expect("model poisoned")
            .commit_activation()?;
        return if activate {
            Ok(status)
        } else {
            deactivate_model(&state.engine)?;
            engine_model_status(&state.engine, false)
        };
    }
    deactivate_model(&state.engine)?;
    state
        .model
        .lock()
        .expect("model poisoned")
        .rollback_activation()?;
    engine_model_status(&state.engine, activate)
}

#[tauri::command]
fn model_deactivate(state: State<'_, AppState>) -> AppResult<ModelStatus> {
    deactivate_model(&state.engine)?;
    Ok(state.model.lock().expect("model poisoned").status())
}
#[tauri::command]
fn model_import(app: AppHandle, state: State<'_, AppState>) -> AppResult<Option<ModelStatus>> {
    let selected = app
        .dialog()
        .file()
        .add_filter("Gói model SoátVăn", &["svmodel"])
        .blocking_pick_file();
    let Some(path) = selected.and_then(|value| value.into_path().ok()) else {
        return Ok(None);
    };
    state.model_cancel.store(false, Ordering::Release);
    let status = install_model_package(&state, &path)?;
    Ok(Some(status))
}
#[tauri::command]
fn model_remove(state: State<'_, AppState>) -> AppResult<ModelStatus> {
    deactivate_model(&state.engine)?;
    state.model.lock().expect("model poisoned").remove()?;
    engine_model_status(&state.engine, false)
}
#[tauri::command]
async fn model_download(app: AppHandle, state: State<'_, AppState>) -> AppResult<ModelStatus> {
    const MAX_MODEL_PACKAGE: u64 = 8 * 1024 * 1024 * 1024;
    let endpoint = option_env!("SOATVAN_MODEL_ENDPOINT").ok_or(AppError::ModelNotConfigured)?;
    let allowlist = option_env!("SOATVAN_MODEL_ALLOWLIST").ok_or(AppError::ModelNotConfigured)?;
    let url = url::Url::parse(endpoint).map_err(|_| AppError::ModelNotConfigured)?;
    let allowed = url.scheme() == "https"
        && url
            .host_str()
            .is_some_and(|host| allowlist.split(',').any(|item| item.trim() == host));
    if !allowed {
        return Err(AppError::ModelNotConfigured);
    }
    state.model_cancel.store(false, Ordering::Release);
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(30))
        .build()
        .map_err(|_| AppError::ModelNotConfigured)?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|_| AppError::InvalidPath)?;
    fs::create_dir_all(&cache)?;
    let cache_key = format!("{:x}", sha2::Sha256::digest(url.as_str().as_bytes()));
    let partial_path = cache.join(format!("model-{}.svmodel.partial", &cache_key[..16]));
    let mut resumed_at = fs::metadata(&partial_path)
        .map(|item| item.len())
        .unwrap_or(0);
    if resumed_at > MAX_MODEL_PACKAGE {
        fs::remove_file(&partial_path)?;
        resumed_at = 0;
    }
    let mut request = client.get(url);
    if resumed_at > 0 {
        request = request.header(reqwest::header::RANGE, format!("bytes={resumed_at}-"));
    }
    let mut response = await_download(request.send(), &state.model_cancel)
        .await?
        .error_for_status()
        .map_err(|_| AppError::ModelPackageInvalid)?;
    let is_partial = response.status() == reqwest::StatusCode::PARTIAL_CONTENT;
    let remaining = response
        .content_length()
        .ok_or(AppError::ModelPackageInvalid)?;
    let total = if is_partial {
        response
            .headers()
            .get(reqwest::header::CONTENT_RANGE)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| parse_content_range(value, resumed_at, remaining))
            .ok_or(AppError::ModelPackageInvalid)?
    } else {
        resumed_at = 0;
        remaining
    };
    if total == 0 || total > MAX_MODEL_PACKAGE {
        return Err(AppError::ModelPackageInvalid);
    }
    let mut package = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(!is_partial)
        .open(&partial_path)?;
    if is_partial {
        package.seek(SeekFrom::End(0))?;
    }
    let mut received = resumed_at;
    while let Some(chunk) = await_download(response.chunk(), &state.model_cancel).await? {
        received = received.saturating_add(chunk.len() as u64);
        if received > total || received > MAX_MODEL_PACKAGE {
            return Err(AppError::ModelPackageInvalid);
        }
        package.write_all(&chunk)?;
        let _ = app.emit(
            "model.progress",
            json!({"received":received,"total":total,"percent":received.saturating_mul(100)/total}),
        );
    }
    package.flush()?;
    package.sync_all()?;
    if received != total {
        return Err(AppError::ModelPackageInvalid);
    }
    drop(package);
    let status = match install_model_package(&state, &partial_path) {
        Ok(status) => status,
        Err(error) => {
            let _ = fs::remove_file(&partial_path);
            return Err(error);
        }
    };
    fs::remove_file(partial_path)?;
    let _ = app.emit("model.state_changed", &status);
    Ok(status)
}

fn parse_content_range(value: &str, resumed_at: u64, content_length: u64) -> Option<u64> {
    let value = value.strip_prefix("bytes ")?;
    let (range, total) = value.split_once('/')?;
    let (start, end) = range.split_once('-')?;
    let start = start.parse::<u64>().ok()?;
    let end = end.parse::<u64>().ok()?;
    let total = total.parse::<u64>().ok()?;
    let expected_length = end.checked_sub(start)?.checked_add(1)?;
    (start == resumed_at
        && end < total
        && end.checked_add(1) == Some(total)
        && expected_length == content_length)
        .then_some(total)
}

async fn await_download<F, T>(future: F, cancelled: &AtomicBool) -> AppResult<T>
where
    F: Future<Output = Result<T, reqwest::Error>>,
{
    if cancelled.load(Ordering::Acquire) {
        return Err(AppError::ModelCancelled);
    }
    tokio::pin!(future);
    loop {
        tokio::select! {
            result = &mut future => return result.map_err(|_| AppError::ModelPackageInvalid),
            _ = tokio::time::sleep(Duration::from_millis(50)) => {
                if cancelled.load(Ordering::Acquire) {
                    return Err(AppError::ModelCancelled);
                }
            }
        }
    }
}

#[tauri::command]
fn model_cancel(state: State<'_, AppState>) -> bool {
    state.model_cancel.store(true, Ordering::Release);
    true
}

fn engine_model_status(engine: &EngineBroker, activate: bool) -> AppResult<ModelStatus> {
    Ok(serde_json::from_value(engine.call(
        "model.status",
        json!({"activate": activate}),
        Duration::from_secs(120),
    )?)?)
}

fn deactivate_model(engine: &EngineBroker) -> AppResult<()> {
    engine.call("model.remove", json!({}), Duration::from_secs(30))?;
    Ok(())
}

fn install_model_package(state: &AppState, package: &Path) -> AppResult<ModelStatus> {
    deactivate_model(&state.engine)?;
    let installed = state
        .model
        .lock()
        .expect("model poisoned")
        .import_with_cancel(package, || state.model_cancel.load(Ordering::Acquire));
    match installed {
        Ok(status) => activate_model(state, status),
        Err(error) => {
            let _ = engine_model_status(&state.engine, true);
            Err(error)
        }
    }
}

fn activate_model(state: &AppState, installed: ModelStatus) -> AppResult<ModelStatus> {
    if installed.state != "installed" {
        return Ok(installed);
    }
    let status = match engine_model_status(&state.engine, true) {
        Ok(status) => status,
        Err(error) => {
            state
                .model
                .lock()
                .expect("model poisoned")
                .rollback_activation()?;
            let _ = engine_model_status(&state.engine, true);
            return Err(error);
        }
    };
    let provisioner = state.model.lock().expect("model poisoned");
    if status.state == "ready" {
        provisioner.commit_activation()?;
        return Ok(status);
    }
    provisioner.rollback_activation()?;
    drop(provisioner);
    let _ = engine_model_status(&state.engine, true);
    Err(AppError::Engine(
        status.code.unwrap_or_else(|| "MODEL_LOAD_FAILED".into()),
    ))
}

fn validate_docx(path: &Path) -> AppResult<PathBuf> {
    if path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
        != Some("docx")
    {
        return Err(AppError::InvalidType);
    }
    let canonical = fs::canonicalize(path).map_err(|_| AppError::InvalidPath)?;
    if !canonical.is_file() {
        return Err(AppError::InvalidPath);
    }
    Ok(canonical)
}

fn output_candidate(source: &Path, index: usize) -> AppResult<PathBuf> {
    let parent = source.parent().ok_or(AppError::InvalidPath)?;
    let stem = source
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(AppError::InvalidPath)?;
    let suffix = if index == 1 {
        String::new()
    } else {
        format!("-{index}")
    };
    Ok(parent.join(format!("{stem}-soat{suffix}.docx")))
}

fn finalize_output(source: &Path, temporary: &Path) -> AppResult<PathBuf> {
    for index in 1..=10_000 {
        let candidate = output_candidate(source, index)?;
        match fs::hard_link(temporary, &candidate) {
            Ok(()) => {
                let _ = fs::remove_file(temporary);
                return Ok(candidate);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(AppError::OutputWrite),
        }
    }
    Err(AppError::OutputWrite)
}

fn finalize_output_or_cleanup(source: &Path, temporary: &Path) -> AppResult<PathBuf> {
    match finalize_output(source, temporary) {
        Ok(output) => Ok(output),
        Err(error) => {
            let _ = fs::remove_file(temporary);
            Err(error)
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let data_root = app.path().app_local_data_dir()?;
            let model_root = data_root.join("models");
            let model = ModelProvisioner::new(model_root);
            model.recover_interrupted_activation()?;
            let engine = EngineBroker::start(app.handle(), &data_root)?;
            app.manage(AppState {
                engine,
                model: Mutex::new(model),
                model_cancel: AtomicBool::new(false),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            choose_document,
            inspect_document,
            start_job,
            cancel_job,
            open_output,
            dictionary_list,
            dictionary_upsert,
            dictionary_delete,
            dictionary_import,
            dictionary_export,
            model_status,
            model_deactivate,
            model_import,
            model_download,
            model_cancel,
            model_remove
        ])
        .build(tauri::generate_context!())
        .expect("failed to build SoatVan");
    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = handle.try_state::<AppState>() {
                state.engine.stop();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn output_name_never_overwrites() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join("văn bản.docx");
        fs::write(&source, b"source").unwrap();
        assert_eq!(
            output_candidate(&source, 1).unwrap().file_name().unwrap(),
            "văn bản-soat.docx"
        );
        let existing = folder.path().join("văn bản-soat.docx");
        fs::write(&existing, b"old").unwrap();
        let temporary = folder.path().join(".temporary.docx");
        fs::write(&temporary, b"new").unwrap();
        let output = finalize_output(&source, &temporary).unwrap();
        assert_eq!(output.file_name().unwrap(), "văn bản-soat-2.docx");
        assert_eq!(fs::read(existing).unwrap(), b"old");
        assert_eq!(fs::read(output).unwrap(), b"new");
        assert!(!temporary.exists());
    }

    #[test]
    fn output_failure_removes_temporary_file() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join(format!("{}.docx", "a".repeat(250)));
        let temporary = folder.path().join(".temporary.docx");
        fs::write(&temporary, b"temporary output").unwrap();

        assert!(matches!(
            finalize_output_or_cleanup(&source, &temporary),
            Err(AppError::OutputWrite)
        ));
        assert!(!temporary.exists());
    }

    #[tokio::test]
    async fn model_download_wait_can_be_cancelled_before_network_returns() {
        let cancelled = std::sync::Arc::new(AtomicBool::new(false));
        let signal = std::sync::Arc::clone(&cancelled);
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(10)).await;
            signal.store(true, Ordering::Release);
        });
        let result = await_download(
            std::future::pending::<Result<(), reqwest::Error>>(),
            &cancelled,
        )
        .await;
        assert!(matches!(result, Err(AppError::ModelCancelled)));
    }

    #[test]
    fn resumed_download_requires_an_exact_complete_content_range() {
        assert_eq!(
            parse_content_range("bytes 100-199/200", 100, 100),
            Some(200)
        );
        assert_eq!(parse_content_range("bytes 100-evil/200", 100, 100), None);
        assert_eq!(parse_content_range("bytes 10-199/200", 100, 190), None);
        assert_eq!(parse_content_range("bytes 100-149/200", 100, 50), None);
        assert_eq!(parse_content_range("items 100-199/200", 100, 100), None);
    }
}
