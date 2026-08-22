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
use std::{
    fs,
    io::Write,
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
    })
}

#[tauri::command]
async fn start_job(
    job_id: String,
    source_path: String,
    preset: String,
    custom_prompt: String,
    state: State<'_, AppState>,
) -> AppResult<JobResult> {
    let source = validate_docx(Path::new(&source_path))?;
    if !matches!(preset.as_str(), "standard" | "administrative" | "spelling") {
        return Err(AppError::Engine("PRESET_INVALID".into()));
    }
    if !custom_prompt.trim().is_empty() {
        return Err(AppError::Engine("MODEL_CLASSIFIER_NOT_READY".into()));
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
            "temporary_output_path": temporary_path, "preset": preset
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
    let output = collision_safe_output(&source)?;
    fs::rename(&temporary_path, &output).map_err(|_| AppError::OutputWrite)?;
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
fn model_status(state: State<'_, AppState>) -> ModelStatus {
    state.model.lock().expect("model poisoned").status()
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
    state
        .model
        .lock()
        .expect("model poisoned")
        .import(&path)
        .map(Some)
}
#[tauri::command]
fn model_remove(state: State<'_, AppState>) -> AppResult<ModelStatus> {
    state.model.lock().expect("model poisoned").remove()
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
        .build()
        .map_err(|_| AppError::ModelNotConfigured)?;
    let mut response = client
        .get(url)
        .send()
        .await
        .map_err(|_| AppError::ModelPackageInvalid)?
        .error_for_status()
        .map_err(|_| AppError::ModelPackageInvalid)?;
    let total = response
        .content_length()
        .ok_or(AppError::ModelPackageInvalid)?;
    if total == 0 || total > MAX_MODEL_PACKAGE {
        return Err(AppError::ModelPackageInvalid);
    }
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|_| AppError::InvalidPath)?;
    fs::create_dir_all(&cache)?;
    let mut package = tempfile::Builder::new()
        .suffix(".svmodel")
        .tempfile_in(cache)?;
    let mut received = 0_u64;
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| AppError::ModelPackageInvalid)?
    {
        if state.model_cancel.load(Ordering::Acquire) {
            return Err(AppError::Engine("MODEL_DOWNLOAD_CANCELLED".into()));
        }
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
    if received != total {
        return Err(AppError::ModelPackageInvalid);
    }
    let status = state
        .model
        .lock()
        .expect("model poisoned")
        .import(package.path())?;
    let _ = app.emit("model.state_changed", &status);
    Ok(status)
}

#[tauri::command]
fn model_cancel(state: State<'_, AppState>) -> bool {
    state.model_cancel.store(true, Ordering::Release);
    true
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

fn collision_safe_output(source: &Path) -> AppResult<PathBuf> {
    let parent = source.parent().ok_or(AppError::InvalidPath)?;
    let stem = source
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(AppError::InvalidPath)?;
    for index in 1..=10_000 {
        let suffix = if index == 1 {
            String::new()
        } else {
            format!("-{index}")
        };
        let candidate = parent.join(format!("{stem}-soat{suffix}.docx"));
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(AppError::OutputWrite)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let model_root = app.path().app_local_data_dir()?.join("models");
            fs::create_dir_all(&model_root)?;
            let engine = EngineBroker::start(app.handle())?;
            app.manage(AppState {
                engine,
                model: Mutex::new(ModelProvisioner::new(model_root)),
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
            collision_safe_output(&source).unwrap().file_name().unwrap(),
            "văn bản-soat.docx"
        );
        fs::write(folder.path().join("văn bản-soat.docx"), b"old").unwrap();
        assert_eq!(
            collision_safe_output(&source).unwrap().file_name().unwrap(),
            "văn bản-soat-2.docx"
        );
    }
}
