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
    collections::HashSet,
    fs,
    fs::OpenOptions,
    future::Future,
    io::{self, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use tokio::sync::Mutex as AsyncMutex;

struct AppState {
    engine: EngineBroker,
    model: Mutex<ModelProvisioner>,
    model_operation: AsyncMutex<()>,
    model_generation: AtomicU64,
    model_cancelled_generation: AtomicU64,
    produced_outputs: Mutex<HashSet<PathBuf>>,
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
    review: Option<ReviewCoverage>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ReviewStatus {
    Complete,
    Partial,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct ReviewCoverage {
    status: ReviewStatus,
    total_chunks: u64,
    reviewed_chunks: u64,
    failed_chunks: u64,
    total_blocks: u64,
    reviewed_blocks: u64,
    failed_blocks: u64,
}

impl ReviewCoverage {
    fn is_consistent(&self) -> bool {
        let chunks_are_consistent =
            self.reviewed_chunks.checked_add(self.failed_chunks) == Some(self.total_chunks);
        let blocks_are_consistent =
            self.reviewed_blocks.checked_add(self.failed_blocks) == Some(self.total_blocks);
        let status_is_consistent = match self.status {
            ReviewStatus::Complete => self.failed_chunks == 0 && self.failed_blocks == 0,
            ReviewStatus::Partial => self.failed_chunks > 0 || self.failed_blocks > 0,
        };
        chunks_are_consistent && blocks_are_consistent && status_is_consistent
    }
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
    #[serde(default)]
    full_review: bool,
    rule_options: RuleOptions,
    ignored_words: Vec<String>,
}

#[derive(Serialize)]
struct SidecarJobParams<'a> {
    job_id: &'a str,
    source_path: &'a Path,
    temporary_output_path: &'a Path,
    preset: &'a str,
    custom_prompt: &'a str,
    use_model: bool,
    full_review: bool,
    rule_config: &'a RuleOptions,
    ignored_words: &'a [String],
}

#[derive(Debug, Serialize, Deserialize)]
struct CustomRule {
    id: String,
    prompt: String,
    created_at: String,
    updated_at: String,
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
        full_review,
        rule_options,
        ignored_words,
    } = request;
    let source = validate_docx(Path::new(&source_path))?;
    if !matches!(preset.as_str(), "standard" | "administrative" | "spelling") {
        return Err(AppError::Engine("PRESET_INVALID".into()));
    }
    validate_custom_prompt(&custom_prompt)?;
    validate_model_job_options(&custom_prompt, use_model, full_review)?;
    if use_model {
        let model_status = engine_model_status(&state.engine, true)?;
        if model_status.state != "ready" || !model_status.capabilities.candidate_filter {
            return Err(AppError::Engine("MODEL_CLASSIFIER_NOT_READY".into()));
        }
        if full_review && !model_status.capabilities.full_review {
            return Err(AppError::Engine("MODEL_FULL_REVIEW_NOT_APPROVED".into()));
        }
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
        serde_json::to_value(SidecarJobParams {
            job_id: &job_id,
            source_path: &controlled_source,
            temporary_output_path: &temporary_path,
            preset: &preset,
            custom_prompt: &custom_prompt,
            use_model,
            full_review,
            rule_config: &rule_options,
            ignored_words: &ignored_words,
        })?,
        // EngineBroker resets this inactivity deadline on every job.progress event.
        Duration::from_secs(10 * 60),
    );
    let response = match response {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_file(&temporary_path);
            return Err(error);
        }
    };
    let (finding_count, counts, review) =
        parse_job_response_or_cleanup(&response, &temporary_path)?;
    let status = job_result_status(finding_count, review.as_ref()).to_owned();
    if finding_count == 0 {
        let _ = fs::remove_file(&temporary_path);
        return Ok(JobResult {
            job_id,
            status,
            output_path: None,
            finding_count: 0,
            counts: json!({}),
            review,
        });
    }
    let output = finalize_output_or_cleanup(&source, &temporary_path)?;
    let output = match register_produced_output(&output, &state.produced_outputs) {
        Ok(output) => output,
        Err(error) => {
            let _ = fs::remove_file(&output);
            return Err(error);
        }
    };
    Ok(JobResult {
        job_id,
        status,
        output_path: Some(user_visible_path(&output).to_string_lossy().into_owned()),
        finding_count,
        counts,
        review,
    })
}

fn parse_job_response(response: &Value) -> AppResult<(u64, Value, Option<ReviewCoverage>)> {
    let finding_count = parse_finding_count(response)?;
    let counts = if finding_count == 0 {
        json!({})
    } else {
        parse_result_counts(response, finding_count)?
    };
    let review = parse_review_coverage(response)?;
    Ok((finding_count, counts, review))
}

fn parse_job_response_or_cleanup(
    response: &Value,
    temporary_output: &Path,
) -> AppResult<(u64, Value, Option<ReviewCoverage>)> {
    match parse_job_response(response) {
        Ok(fields) => Ok(fields),
        Err(error) => {
            let _ = fs::remove_file(temporary_output);
            Err(error)
        }
    }
}

fn parse_finding_count(response: &Value) -> AppResult<u64> {
    response
        .get("finding_count")
        .and_then(Value::as_u64)
        .ok_or(AppError::EngineProtocol)
}

fn parse_count_group(value: Option<&Value>, finding_count: u64) -> AppResult<Value> {
    let group = value
        .and_then(Value::as_object)
        .ok_or(AppError::EngineProtocol)?;
    let total = group
        .values()
        .try_fold(0_u64, |total, count| total.checked_add(count.as_u64()?));
    if total != Some(finding_count) {
        return Err(AppError::EngineProtocol);
    }
    Ok(Value::Object(group.clone()))
}

fn parse_result_counts(response: &Value, finding_count: u64) -> AppResult<Value> {
    let counts = response
        .get("counts")
        .and_then(Value::as_object)
        .ok_or(AppError::EngineProtocol)?;
    let category = parse_count_group(counts.get("category"), finding_count)?;
    let origin = parse_count_group(counts.get("origin"), finding_count)?;
    Ok(json!({"category": category, "origin": origin}))
}

fn parse_review_coverage(response: &Value) -> AppResult<Option<ReviewCoverage>> {
    let value = response.get("review").cloned().unwrap_or(Value::Null);
    let coverage: Option<ReviewCoverage> =
        serde_json::from_value(value).map_err(|_| AppError::EngineProtocol)?;
    if coverage
        .as_ref()
        .is_some_and(|coverage| !coverage.is_consistent())
    {
        return Err(AppError::EngineProtocol);
    }
    Ok(coverage)
}

fn job_result_status(finding_count: u64, review: Option<&ReviewCoverage>) -> &'static str {
    if review.is_some_and(|coverage| coverage.status == ReviewStatus::Partial) {
        "partial"
    } else if finding_count == 0 {
        "no_findings"
    } else {
        "completed"
    }
}

fn validate_model_job_options(
    custom_prompt: &str,
    use_model: bool,
    full_review: bool,
) -> AppResult<()> {
    if !custom_prompt.trim().is_empty() && !use_model {
        return Err(AppError::Engine("CUSTOM_PROMPT_REQUIRES_MODEL".into()));
    }
    if full_review && !use_model {
        return Err(AppError::Engine("FULL_REVIEW_REQUIRES_MODEL".into()));
    }
    Ok(())
}

fn validate_custom_prompt(custom_prompt: &str) -> AppResult<()> {
    if custom_prompt.chars().count() > 4200 {
        return Err(AppError::Engine("CUSTOM_PROMPT_TOO_LONG".into()));
    }
    Ok(())
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
fn open_output(
    app: AppHandle,
    path: String,
    reveal: bool,
    state: State<'_, AppState>,
) -> AppResult<()> {
    let canonical = validate_produced_output(Path::new(&path), &state.produced_outputs)?;
    if reveal {
        let shell_path = user_visible_path(&canonical);
        app.opener()
            .reveal_item_in_dir(shell_path)
            .map_err(|_| AppError::InvalidPath)?;
    } else {
        let shell_path = user_visible_path(&canonical);
        app.opener()
            .open_path(shell_path.to_string_lossy(), None::<&str>)
            .map_err(|_| AppError::InvalidPath)?;
    }
    Ok(())
}

#[tauri::command]
async fn custom_rule_list(state: State<'_, AppState>) -> AppResult<Vec<CustomRule>> {
    let value = state
        .engine
        .call("custom_rule.list", json!({}), Duration::from_secs(5))?;
    Ok(serde_json::from_value(value["entries"].clone())?)
}

#[tauri::command]
async fn custom_rule_upsert(
    id: Option<String>,
    prompt: String,
    state: State<'_, AppState>,
) -> AppResult<CustomRule> {
    Ok(serde_json::from_value(state.engine.call(
        "custom_rule.upsert",
        json!({"id": id, "prompt": prompt}),
        Duration::from_secs(5),
    )?)?)
}

#[tauri::command]
async fn custom_rule_delete(id: String, state: State<'_, AppState>) -> AppResult<bool> {
    Ok(state.engine.call(
        "custom_rule.delete",
        json!({"id": id}),
        Duration::from_secs(5),
    )?["deleted"]
        .as_bool()
        .unwrap_or(false))
}

#[tauri::command]
async fn model_status(activate: bool, state: State<'_, AppState>) -> AppResult<ModelStatus> {
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
async fn model_deactivate(state: State<'_, AppState>) -> AppResult<ModelStatus> {
    deactivate_model(&state.engine)?;
    Ok(state.model.lock().expect("model poisoned").status())
}
#[tauri::command]
async fn model_import(
    app: AppHandle,
    state: State<'_, AppState>,
) -> AppResult<Option<ModelStatus>> {
    let selected = app
        .dialog()
        .file()
        .add_filter("Gói model SoátVăn", &["svmodel", "zip", "gguf"])
        .blocking_pick_file();
    let Some(path) = selected.and_then(|value| value.into_path().ok()) else {
        return Ok(None);
    };
    let _operation = state
        .model_operation
        .try_lock()
        .map_err(|_| AppError::ModelOperationInProgress)?;
    let generation = next_model_generation(&state.model_generation);
    let status = install_model_package(&state, &path, None, generation)?;
    Ok(Some(status))
}
#[tauri::command]
async fn model_remove(
    app: AppHandle,
    state: State<'_, AppState>,
    model_id: Option<String>,
) -> AppResult<ModelStatus> {
    let _ = model_id;
    cancel_current_model_operation(&state);
    let _operation = state.model_operation.lock().await;
    deactivate_model(&state.engine)?;
    state.model.lock().expect("model poisoned").remove()?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|_| AppError::InvalidPath)?;
    clean_model_partial_cache(&cache)?;
    engine_model_status(&state.engine, false)
}
#[tauri::command]
async fn model_download(
    app: AppHandle,
    state: State<'_, AppState>,
    model_id: Option<String>,
) -> AppResult<ModelStatus> {
    const MAX_MODEL_PACKAGE: u64 = 8 * 1024 * 1024 * 1024;
    let chosen_id = model_id.unwrap_or_else(|| "gemma-4-e4b".into());
    let default_endpoint = match chosen_id.as_str() {
        "gemma-4-e2b" => "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
        "gemma-4-12b" => "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf",
        "gemma-4-e4b" => "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        _ => return Err(AppError::ModelNotConfigured),
    };
    let endpoint = option_env!("SOATVAN_MODEL_ENDPOINT").unwrap_or(default_endpoint);
    let default_allowlist =
        "huggingface.co,cdn-lfs.huggingface.co,github.com,objects.githubusercontent.com";
    let allowlist = option_env!("SOATVAN_MODEL_ALLOWLIST").unwrap_or(default_allowlist);
    let allowed_hosts = parse_model_allowlist(allowlist);
    let url = url::Url::parse(endpoint).map_err(|_| AppError::ModelNotConfigured)?;
    if !model_url_is_allowed(&url, &allowed_hosts) {
        return Err(AppError::ModelNotConfigured);
    }
    let _operation = state
        .model_operation
        .try_lock()
        .map_err(|_| AppError::ModelOperationInProgress)?;
    let generation = next_model_generation(&state.model_generation);
    let redirect_hosts = allowed_hosts.clone();
    let client = reqwest::Client::builder()
        .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) SoatVan/0.1.5")
        .redirect(reqwest::redirect::Policy::custom(move |attempt| {
            if attempt.previous().len() >= 10 {
                attempt.error("too many model download redirects")
            } else if model_url_is_allowed(attempt.url(), &redirect_hosts) {
                attempt.follow()
            } else {
                attempt.error("model download redirect target is not allowed")
            }
        }))
        .connect_timeout(Duration::from_secs(30))
        .build()
        .map_err(|_| AppError::ModelNotConfigured)?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|_| AppError::InvalidPath)?;
    fs::create_dir_all(&cache)?;
    let cache_identity = format!("{chosen_id}\n{url}");
    let cache_key = format!("{:x}", sha2::Sha256::digest(cache_identity.as_bytes()));
    let partial_path = cache.join(format!(
        "model-{chosen_id}-{}.svmodel.partial",
        &cache_key[..16]
    ));
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
    let mut response = await_download(
        request.send(),
        &state.model_cancelled_generation,
        generation,
    )
    .await?
    .error_for_status()
    .map_err(|_| AppError::ModelPackageInvalid)?;
    if !model_url_is_allowed(response.url(), &allowed_hosts) {
        return Err(AppError::ModelNotConfigured);
    }
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
    let mut last_emitted_percent = 0;
    let mut last_emitted_time = std::time::Instant::now();
    while let Some(chunk) = await_download(
        response.chunk(),
        &state.model_cancelled_generation,
        generation,
    )
    .await?
    {
        received = received.saturating_add(chunk.len() as u64);
        if received > total || received > MAX_MODEL_PACKAGE {
            return Err(AppError::ModelPackageInvalid);
        }
        package.write_all(&chunk)?;
        let percent = received.saturating_mul(100) / total;
        if percent != last_emitted_percent || last_emitted_time.elapsed().as_millis() >= 100 {
            last_emitted_percent = percent;
            last_emitted_time = std::time::Instant::now();
            let _ = app.emit(
                "model.progress",
                json!({"received": received, "total": total, "percent": percent}),
            );
        }
    }
    let _ = app.emit(
        "model.progress",
        json!({"received": total, "total": total, "percent": 100}),
    );
    package.flush()?;
    package.sync_all()?;
    if received != total {
        return Err(AppError::ModelPackageInvalid);
    }
    drop(package);
    let status =
        match install_model_package(&state, &partial_path, Some(chosen_id.as_str()), generation) {
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

fn parse_model_allowlist(value: &str) -> HashSet<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|host| !host.is_empty())
        .map(|host| host.to_ascii_lowercase())
        .collect()
}

fn model_url_is_allowed(url: &url::Url, allowed_hosts: &HashSet<String>) -> bool {
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default() == Some(443)
        && url
            .host_str()
            .is_some_and(|host| allowed_hosts.contains(&host.to_ascii_lowercase()))
}

fn next_model_generation(model_generation: &AtomicU64) -> u64 {
    model_generation
        .fetch_add(1, Ordering::AcqRel)
        .wrapping_add(1)
}

fn model_operation_cancelled(cancelled_generation: &AtomicU64, generation: u64) -> bool {
    cancelled_generation.load(Ordering::Acquire) == generation
}

fn clean_model_partial_cache(cache: &Path) -> AppResult<()> {
    if !cache.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(cache)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if !file_type.is_file() && !file_type.is_symlink() {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        let is_scoped_partial = name.len() <= 128
            && name.starts_with("model-")
            && name.ends_with(".svmodel.partial")
            && name.bytes().all(|byte| {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || matches!(byte, b'-' | b'_' | b'.')
            });
        if is_scoped_partial {
            fs::remove_file(entry.path())?;
        }
    }
    Ok(())
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

async fn await_download<F, T>(
    future: F,
    cancelled_generation: &AtomicU64,
    generation: u64,
) -> AppResult<T>
where
    F: Future<Output = Result<T, reqwest::Error>>,
{
    if model_operation_cancelled(cancelled_generation, generation) {
        return Err(AppError::ModelCancelled);
    }
    tokio::pin!(future);
    loop {
        tokio::select! {
            result = &mut future => return result.map_err(|_| AppError::ModelPackageInvalid),
            _ = tokio::time::sleep(Duration::from_millis(50)) => {
                if model_operation_cancelled(cancelled_generation, generation) {
                    return Err(AppError::ModelCancelled);
                }
            }
        }
    }
}

#[tauri::command]
fn model_cancel(state: State<'_, AppState>) -> bool {
    cancel_current_model_operation(&state)
}

fn cancel_current_model_operation(state: &AppState) -> bool {
    let before = state.model_generation.load(Ordering::Acquire);
    if before == 0 || state.model_operation.try_lock().is_ok() {
        return false;
    }
    let after = state.model_generation.load(Ordering::Acquire);
    if before != after {
        return false;
    }
    state
        .model_cancelled_generation
        .store(before, Ordering::Release);
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

fn install_model_package(
    state: &AppState,
    package: &Path,
    expected_model_id: Option<&str>,
    generation: u64,
) -> AppResult<ModelStatus> {
    deactivate_model(&state.engine)?;
    let installed = state
        .model
        .lock()
        .expect("model poisoned")
        .import_with_cancel(package, expected_model_id, || {
            model_operation_cancelled(&state.model_cancelled_generation, generation)
        });
    match installed {
        Ok(status) => activate_model(state, status, generation),
        Err(error) => {
            let _ = engine_model_status(&state.engine, true);
            Err(error)
        }
    }
}

fn activate_model(
    state: &AppState,
    installed: ModelStatus,
    generation: u64,
) -> AppResult<ModelStatus> {
    if installed.state != "installed" {
        return Ok(installed);
    }
    if model_operation_cancelled(&state.model_cancelled_generation, generation) {
        state
            .model
            .lock()
            .expect("model poisoned")
            .rollback_activation()?;
        let _ = engine_model_status(&state.engine, true);
        return Err(AppError::ModelCancelled);
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
        if model_operation_cancelled(&state.model_cancelled_generation, generation) {
            drop(provisioner);
            deactivate_model(&state.engine)?;
            state
                .model
                .lock()
                .expect("model poisoned")
                .rollback_activation()?;
            let _ = engine_model_status(&state.engine, true);
            return Err(AppError::ModelCancelled);
        }
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

// The engine accepts up to 256 MiB uncompressed input. Annotation can grow
// document.xml and add comments, so output validation needs bounded headroom.
const MAX_DOCX_OUTPUT_ARCHIVE_BYTES: u64 = 160 * 1024 * 1024;
const MAX_DOCX_OUTPUT_UNCOMPRESSED_BYTES: u64 = 320 * 1024 * 1024;
const MAX_DOCX_METADATA_PART_BYTES: u64 = 16 * 1024 * 1024;
const MAX_DOCX_ENTRIES: usize = 10_010;
const MAX_DOCX_COMPRESSION_RATIO: u64 = 200;
const _: () = assert!(MAX_DOCX_OUTPUT_UNCOMPRESSED_BYTES > 256 * 1024 * 1024);
const _: () = assert!(MAX_DOCX_ENTRIES > 10_000);
const DOCX_MAIN_CONTENT_TYPE: &[u8] =
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml";

fn read_docx_part(
    archive: &mut zip::ZipArchive<fs::File>,
    name: &str,
    max_size: u64,
) -> Result<Vec<u8>, ()> {
    let mut part = archive.by_name(name).map_err(|_| ())?;
    let expected_size = part.size();
    if part.is_dir() || expected_size == 0 || expected_size > max_size {
        return Err(());
    }
    let mut content = Vec::with_capacity(expected_size as usize);
    part.read_to_end(&mut content).map_err(|_| ())?;
    if content.len() as u64 != expected_size {
        return Err(());
    }
    Ok(content)
}

fn verify_docx_part(
    archive: &mut zip::ZipArchive<fs::File>,
    name: &str,
    max_size: u64,
) -> Result<(), ()> {
    let mut part = archive.by_name(name).map_err(|_| ())?;
    let expected_size = part.size();
    if part.is_dir() || expected_size == 0 || expected_size > max_size {
        return Err(());
    }
    let copied = io::copy(&mut part, &mut io::sink()).map_err(|_| ())?;
    if copied != expected_size {
        return Err(());
    }
    Ok(())
}

fn contains_bytes(content: &[u8], expected: &[u8]) -> bool {
    content
        .windows(expected.len())
        .any(|candidate| candidate == expected)
}

fn validate_docx_package(path: &Path) -> Result<(), ()> {
    let metadata = fs::metadata(path).map_err(|_| ())?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_DOCX_OUTPUT_ARCHIVE_BYTES
    {
        return Err(());
    }
    let file = fs::File::open(path).map_err(|_| ())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|_| ())?;
    if archive.is_empty() || archive.len() > MAX_DOCX_ENTRIES {
        return Err(());
    }

    let mut names = HashSet::with_capacity(archive.len());
    let mut total_uncompressed = 0_u64;
    for index in 0..archive.len() {
        let part = archive.by_index(index).map_err(|_| ())?;
        if part.enclosed_name().is_none() || !names.insert(part.name().to_owned()) {
            return Err(());
        }
        let size = part.size();
        let compressed_size = part.compressed_size();
        total_uncompressed = total_uncompressed.checked_add(size).ok_or(())?;
        let exceeds_ratio = size > 0
            && (compressed_size == 0
                || compressed_size
                    .checked_mul(MAX_DOCX_COMPRESSION_RATIO)
                    .map_or(true, |maximum| size > maximum));
        if total_uncompressed > MAX_DOCX_OUTPUT_UNCOMPRESSED_BYTES || exceeds_ratio {
            return Err(());
        }
    }

    let content_types = read_docx_part(
        &mut archive,
        "[Content_Types].xml",
        MAX_DOCX_METADATA_PART_BYTES,
    )?;
    if !contains_bytes(&content_types, DOCX_MAIN_CONTENT_TYPE) {
        return Err(());
    }

    let relationships = read_docx_part(&mut archive, "_rels/.rels", MAX_DOCX_METADATA_PART_BYTES)?;
    if !contains_bytes(&relationships, b"officeDocument")
        || !contains_bytes(&relationships, b"word/document.xml")
    {
        return Err(());
    }

    verify_docx_part(
        &mut archive,
        "word/document.xml",
        MAX_DOCX_OUTPUT_UNCOMPRESSED_BYTES,
    )
}

fn register_produced_output(
    output: &Path,
    produced_outputs: &Mutex<HashSet<PathBuf>>,
) -> AppResult<PathBuf> {
    let canonical = validate_docx(output).map_err(|_| AppError::OutputWrite)?;
    produced_outputs
        .lock()
        .map_err(|_| AppError::OutputWrite)?
        .insert(canonical.clone());
    Ok(canonical)
}

fn validate_produced_output(
    path: &Path,
    produced_outputs: &Mutex<HashSet<PathBuf>>,
) -> AppResult<PathBuf> {
    let canonical = validate_docx(path)?;
    if !produced_outputs
        .lock()
        .map_err(|_| AppError::InvalidPath)?
        .contains(&canonical)
    {
        return Err(AppError::InvalidPath);
    }
    validate_docx_package(&canonical).map_err(|_| AppError::InvalidPath)?;
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

fn user_visible_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        let value = path.to_string_lossy();
        if let Some(network_path) = value.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{network_path}"));
        }
        if let Some(dos_path) = value.strip_prefix(r"\\?\") {
            if dos_path.as_bytes().get(1) == Some(&b':') {
                return PathBuf::from(dos_path);
            }
        }
    }
    path.to_path_buf()
}

fn copy_output_no_clobber(source: &Path, destination: &Path) -> io::Result<()> {
    let mut input = fs::File::open(source)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    let copied = (|| {
        io::copy(&mut input, &mut output)?;
        output.flush()?;
        output.sync_all()
    })();
    if let Err(error) = copied {
        drop(output);
        let _ = fs::remove_file(destination);
        return Err(error);
    }
    Ok(())
}

fn install_output_no_clobber(temporary: &Path, candidate: &Path) -> io::Result<()> {
    #[cfg(windows)]
    {
        use std::{iter, os::windows::ffi::OsStrExt};
        use windows_sys::Win32::Storage::FileSystem::MoveFileExW;

        let source = temporary
            .as_os_str()
            .encode_wide()
            .chain(iter::once(0))
            .collect::<Vec<_>>();
        let destination = candidate
            .as_os_str()
            .encode_wide()
            .chain(iter::once(0))
            .collect::<Vec<_>>();
        let moved = unsafe { MoveFileExW(source.as_ptr(), destination.as_ptr(), 0) };
        match moved {
            value if value != 0 => return Ok(()),
            _ if candidate.exists() => {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    io::Error::last_os_error(),
                ));
            }
            // Some network providers cannot atomically rename a temporary file. The
            // create_new fallback still guarantees that an existing output is never
            // overwritten.
            _ => {}
        }
    }

    #[cfg(not(windows))]
    {
        match fs::hard_link(temporary, candidate) {
            Ok(()) => return Ok(()),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => return Err(error),
            Err(_) => {}
        }
    }

    copy_output_no_clobber(temporary, candidate)
}

fn finalize_output(source: &Path, temporary: &Path) -> AppResult<PathBuf> {
    validate_docx_package(temporary).map_err(|_| AppError::OutputWrite)?;
    for index in 1..=10_000 {
        let candidate = output_candidate(source, index)?;
        match install_output_no_clobber(temporary, &candidate) {
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
                model_operation: AsyncMutex::new(()),
                model_generation: AtomicU64::new(0),
                model_cancelled_generation: AtomicU64::new(0),
                produced_outputs: Mutex::new(HashSet::new()),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            choose_document,
            inspect_document,
            start_job,
            cancel_job,
            open_output,
            custom_rule_list,
            custom_rule_upsert,
            custom_rule_delete,
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

    fn write_test_docx(path: &Path) {
        let file = fs::File::create(path).expect("create DOCX");
        let mut archive = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default();
        archive
            .start_file("[Content_Types].xml", options)
            .expect("start content types");
        archive
            .write_all(
                br#"<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"#,
            )
            .expect("write content types");
        archive
            .start_file("_rels/.rels", options)
            .expect("start relationships");
        archive
            .write_all(
                br#"<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"#,
            )
            .expect("write relationships");
        archive
            .start_file("word/document.xml", options)
            .expect("start document");
        archive
            .write_all(
                br#"<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>"#,
            )
            .expect("write document");
        archive.finish().expect("finish DOCX");
    }

    fn start_job_request_json() -> Value {
        json!({
            "jobId": "job-1",
            "sourcePath": "document.docx",
            "preset": "standard",
            "customPrompt": "",
            "useModel": true,
            "ruleOptions": {
                "technical": true,
                "repeated_words": true,
                "confusions": true,
                "syllables": true,
                "administrative_capitalization": false
            },
            "ignoredWords": []
        })
    }

    #[test]
    fn full_review_defaults_to_false_for_older_clients() {
        let request: StartJobRequest =
            serde_json::from_value(start_job_request_json()).expect("deserialize request");

        assert!(!request.full_review);
    }

    #[test]
    fn full_review_uses_camel_case_at_the_tauri_boundary() {
        let mut value = start_job_request_json();
        value["fullReview"] = json!(true);

        let request: StartJobRequest = serde_json::from_value(value).expect("deserialize request");

        assert!(request.full_review);
    }

    #[test]
    fn full_review_requires_an_enabled_model() {
        let error = validate_model_job_options("", false, true).expect_err("must reject request");

        assert_eq!(error.to_string(), "FULL_REVIEW_REQUIRES_MODEL");
        assert!(validate_model_job_options("", true, true).is_ok());
    }

    #[test]
    fn custom_prompt_transport_accepts_saved_rule_budget_and_rejects_overflow() {
        assert!(validate_custom_prompt(&"á".repeat(4200)).is_ok());
        let error = validate_custom_prompt(&"á".repeat(4201)).expect_err("must reject overflow");
        assert_eq!(error.to_string(), "CUSTOM_PROMPT_TOO_LONG");
    }

    #[test]
    fn sidecar_job_params_include_full_review() {
        let rules = RuleOptions {
            technical: true,
            repeated_words: true,
            confusions: true,
            syllables: true,
            administrative_capitalization: false,
        };
        let ignored_words = vec!["SoátVăn".to_owned()];
        let payload = serde_json::to_value(SidecarJobParams {
            job_id: "job-1",
            source_path: Path::new("source.docx"),
            temporary_output_path: Path::new("output.docx"),
            preset: "standard",
            custom_prompt: "",
            use_model: true,
            full_review: true,
            rule_config: &rules,
            ignored_words: &ignored_words,
        })
        .expect("serialize sidecar params");

        assert_eq!(payload["full_review"], true);
        assert_eq!(payload["use_model"], true);
    }

    fn review_coverage(status: ReviewStatus) -> ReviewCoverage {
        ReviewCoverage {
            status,
            total_chunks: 4,
            reviewed_chunks: 3,
            failed_chunks: 1,
            total_blocks: 20,
            reviewed_blocks: 16,
            failed_blocks: 4,
        }
    }

    #[test]
    fn sidecar_review_coverage_is_typed_and_serializable() {
        let response = json!({
            "review": {
                "status": "partial",
                "total_chunks": 4,
                "reviewed_chunks": 3,
                "failed_chunks": 1,
                "total_blocks": 20,
                "reviewed_blocks": 16,
                "failed_blocks": 4
            }
        });

        let coverage = parse_review_coverage(&response)
            .expect("deserialize coverage")
            .expect("coverage present");
        assert_eq!(coverage, review_coverage(ReviewStatus::Partial));

        let serialized = serde_json::to_value(JobResult {
            job_id: "job-1".to_owned(),
            status: "partial".to_owned(),
            output_path: None,
            finding_count: 0,
            counts: json!({}),
            review: Some(coverage),
        })
        .expect("serialize job result");
        assert_eq!(serialized["review"]["status"], "partial");
        assert_eq!(serialized["review"]["failed_blocks"], 4);

        let block_only_failure = json!({
            "review": {
                "status": "partial",
                "total_chunks": 1,
                "reviewed_chunks": 1,
                "failed_chunks": 0,
                "total_blocks": 2,
                "reviewed_blocks": 1,
                "failed_blocks": 1
            }
        });
        assert_eq!(
            parse_review_coverage(&block_only_failure)
                .unwrap()
                .unwrap()
                .status,
            ReviewStatus::Partial
        );
    }

    #[test]
    fn missing_or_null_sidecar_review_remains_optional() {
        assert!(parse_review_coverage(&json!({})).unwrap().is_none());
        assert!(parse_review_coverage(&json!({"review": null}))
            .unwrap()
            .is_none());
    }

    #[test]
    fn malformed_or_inconsistent_review_metadata_is_an_engine_protocol_error() {
        let valid_complete = json!({
            "review": {
                "status": "complete",
                "total_chunks": 4,
                "reviewed_chunks": 4,
                "failed_chunks": 0,
                "total_blocks": 20,
                "reviewed_blocks": 20,
                "failed_blocks": 0
            }
        });
        assert_eq!(
            parse_review_coverage(&valid_complete)
                .unwrap()
                .unwrap()
                .status,
            ReviewStatus::Complete
        );

        for review in [
            json!("partial"),
            json!({
                "status": "unknown",
                "total_chunks": 1,
                "reviewed_chunks": 1,
                "failed_chunks": 0,
                "total_blocks": 1,
                "reviewed_blocks": 1,
                "failed_blocks": 0
            }),
            json!({
                "status": "partial",
                "total_chunks": 4,
                "reviewed_chunks": 2,
                "failed_chunks": 1,
                "total_blocks": 20,
                "reviewed_blocks": 16,
                "failed_blocks": 4
            }),
            json!({
                "status": "partial",
                "total_chunks": 4,
                "reviewed_chunks": 3,
                "failed_chunks": 1,
                "total_blocks": 20,
                "reviewed_blocks": 18,
                "failed_blocks": 1
            }),
            json!({
                "status": "complete",
                "total_chunks": 4,
                "reviewed_chunks": 3,
                "failed_chunks": 1,
                "total_blocks": 20,
                "reviewed_blocks": 16,
                "failed_blocks": 4
            }),
            json!({
                "status": "partial",
                "total_chunks": 4,
                "reviewed_chunks": 4,
                "failed_chunks": 0,
                "total_blocks": 20,
                "reviewed_blocks": 20,
                "failed_blocks": 0
            }),
            json!({
                "status": "partial",
                "total_chunks": u64::MAX,
                "reviewed_chunks": u64::MAX,
                "failed_chunks": 1,
                "total_blocks": 1,
                "reviewed_blocks": 0,
                "failed_blocks": 1
            }),
        ] {
            let error = parse_review_coverage(&json!({"review": review}))
                .expect_err("must reject malformed review metadata");
            assert_eq!(error.to_string(), "ENGINE_PROTOCOL_ERROR");
        }
    }

    #[test]
    fn malformed_review_metadata_removes_the_temporary_output() {
        let folder = tempfile::tempdir().unwrap();
        let temporary = folder.path().join(".temporary.docx");
        fs::write(&temporary, b"temporary engine output").unwrap();

        let error = parse_job_response_or_cleanup(
            &json!({"finding_count": 0, "review": {"status": "partial"}}),
            &temporary,
        )
        .expect_err("must reject malformed review metadata");
        assert_eq!(error.to_string(), "ENGINE_PROTOCOL_ERROR");
        assert!(!temporary.exists());
    }

    #[test]
    fn sidecar_finding_count_is_required_and_must_be_an_unsigned_integer() {
        assert_eq!(
            parse_finding_count(&json!({"finding_count": 3})).unwrap(),
            3
        );
        for response in [
            json!({}),
            json!({"finding_count": null}),
            json!({"finding_count": "3"}),
            json!({"finding_count": 3.0}),
            json!({"finding_count": -1}),
        ] {
            let error = parse_finding_count(&response).expect_err("must reject invalid count");
            assert_eq!(error.to_string(), "ENGINE_PROTOCOL_ERROR");
        }
    }

    #[test]
    fn positive_results_require_consistent_nonnegative_integer_counts() {
        let response = json!({
            "finding_count": 3,
            "counts": {
                "category": {"spelling": 2, "technical": 1},
                "origin": {"rule": 2, "llm": 1},
                "future_engine_metadata": "ignored"
            }
        });
        let (finding_count, counts, review) = parse_job_response(&response).unwrap();
        assert_eq!(finding_count, 3);
        assert_eq!(
            counts,
            json!({
                "category": {"spelling": 2, "technical": 1},
                "origin": {"rule": 2, "llm": 1}
            })
        );
        assert!(review.is_none());

        for counts in [
            json!(null),
            json!([]),
            json!({"category": {"spelling": 3}}),
            json!({"origin": {"rule": 3}}),
            json!({"category": [], "origin": {"rule": 3}}),
            json!({"category": {"spelling": -1}, "origin": {"rule": 3}}),
            json!({"category": {"spelling": 3.0}, "origin": {"rule": 3}}),
            json!({"category": {"spelling": "3"}, "origin": {"rule": 3}}),
            json!({"category": {"spelling": 2}, "origin": {"rule": 3}}),
            json!({"category": {"spelling": 3}, "origin": {"rule": 2}}),
        ] {
            let error = parse_job_response(&json!({
                "finding_count": 3,
                "counts": counts
            }))
            .expect_err("must reject malformed counts");
            assert_eq!(error.to_string(), "ENGINE_PROTOCOL_ERROR");
        }
    }

    #[test]
    fn malformed_result_counts_remove_the_temporary_output() {
        let folder = tempfile::tempdir().unwrap();
        let temporary = folder.path().join(".temporary.docx");
        fs::write(&temporary, b"temporary engine output").unwrap();

        let error = parse_job_response_or_cleanup(
            &json!({
                "finding_count": 1,
                "counts": {"category": {"spelling": 1}, "origin": {"rule": -1}}
            }),
            &temporary,
        )
        .expect_err("must reject malformed counts");
        assert_eq!(error.to_string(), "ENGINE_PROTOCOL_ERROR");
        assert!(!temporary.exists());
    }

    #[test]
    fn partial_zero_finding_result_is_not_no_findings() {
        let coverage = review_coverage(ReviewStatus::Partial);

        assert_eq!(job_result_status(0, Some(&coverage)), "partial");
        assert_eq!(job_result_status(2, Some(&coverage)), "partial");
    }

    #[test]
    fn complete_zero_finding_result_remains_no_findings() {
        let coverage = review_coverage(ReviewStatus::Complete);

        assert_eq!(job_result_status(0, Some(&coverage)), "no_findings");
        assert_eq!(job_result_status(0, None), "no_findings");
        assert_eq!(job_result_status(2, Some(&coverage)), "completed");
    }

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
        write_test_docx(&temporary);
        let expected = fs::read(&temporary).unwrap();
        let output = finalize_output(&source, &temporary).unwrap();
        assert_eq!(output.file_name().unwrap(), "văn bản-soat-2.docx");
        assert_eq!(fs::read(existing).unwrap(), b"old");
        assert_eq!(fs::read(output).unwrap(), expected);
        assert!(!temporary.exists());
    }

    #[cfg(windows)]
    #[test]
    fn shell_paths_hide_windows_verbatim_prefixes() {
        assert_eq!(
            user_visible_path(Path::new(r"\\?\C:\Tai lieu\ket-qua.docx")),
            PathBuf::from(r"C:\Tai lieu\ket-qua.docx")
        );
        assert_eq!(
            user_visible_path(Path::new(r"\\?\UNC\server\share\ket-qua.docx")),
            PathBuf::from(r"\\server\share\ket-qua.docx")
        );
    }

    #[test]
    fn copy_fallback_never_overwrites_an_existing_output() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join("temporary.docx");
        let destination = folder.path().join("result.docx");
        fs::write(&source, b"new").unwrap();
        fs::write(&destination, b"existing").unwrap();

        let error = copy_output_no_clobber(&source, &destination)
            .expect_err("an existing output must not be overwritten");
        assert_eq!(error.kind(), io::ErrorKind::AlreadyExists);
        assert_eq!(fs::read(destination).unwrap(), b"existing");
    }

    #[test]
    fn output_failure_removes_temporary_file() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join(format!("{}.docx", "a".repeat(250)));
        let temporary = folder.path().join(".temporary.docx");
        write_test_docx(&temporary);

        assert!(matches!(
            finalize_output_or_cleanup(&source, &temporary),
            Err(AppError::OutputWrite)
        ));
        assert!(!temporary.exists());
    }

    #[test]
    fn invalid_docx_output_is_rejected_and_cleaned_up() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join("source.docx");
        let temporary = folder.path().join(".temporary.docx");
        fs::write(&temporary, b"this is not an OOXML package").unwrap();

        assert!(matches!(
            finalize_output_or_cleanup(&source, &temporary),
            Err(AppError::OutputWrite)
        ));
        assert!(!temporary.exists());
        assert!(!folder.path().join("source-soat.docx").exists());
    }

    #[test]
    fn only_registered_regular_docx_outputs_can_be_opened() {
        let folder = tempfile::tempdir().unwrap();
        let output = folder.path().join("source-soat.docx");
        let unregistered = folder.path().join("other-soat.docx");
        let docx_directory = folder.path().join("folder.docx");
        write_test_docx(&output);
        write_test_docx(&unregistered);
        fs::create_dir(&docx_directory).unwrap();
        let produced_outputs = Mutex::new(HashSet::new());

        let registered = register_produced_output(&output, &produced_outputs).unwrap();
        assert_eq!(
            validate_produced_output(&output, &produced_outputs).unwrap(),
            registered
        );
        assert!(matches!(
            validate_produced_output(&unregistered, &produced_outputs),
            Err(AppError::InvalidPath)
        ));
        assert!(matches!(
            validate_produced_output(&docx_directory, &produced_outputs),
            Err(AppError::InvalidPath)
        ));

        fs::write(&output, b"corrupted after publication").unwrap();
        assert!(matches!(
            validate_produced_output(&output, &produced_outputs),
            Err(AppError::InvalidPath)
        ));
    }

    #[tokio::test]
    async fn model_download_wait_can_be_cancelled_before_network_returns() {
        let cancelled_generation = std::sync::Arc::new(AtomicU64::new(0));
        let signal = std::sync::Arc::clone(&cancelled_generation);
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(10)).await;
            signal.store(7, Ordering::Release);
        });
        let result = await_download(
            std::future::pending::<Result<(), reqwest::Error>>(),
            &cancelled_generation,
            7,
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

    #[test]
    fn model_download_allowlist_applies_to_initial_and_redirect_urls() {
        let hosts = parse_model_allowlist("huggingface.co, cdn-lfs.huggingface.co");
        assert!(model_url_is_allowed(
            &url::Url::parse("https://huggingface.co/model").unwrap(),
            &hosts
        ));
        assert!(model_url_is_allowed(
            &url::Url::parse("https://cdn-lfs.huggingface.co/model").unwrap(),
            &hosts
        ));
        assert!(!model_url_is_allowed(
            &url::Url::parse("https://huggingface.co.evil.test/model").unwrap(),
            &hosts
        ));
        assert!(!model_url_is_allowed(
            &url::Url::parse("http://huggingface.co/model").unwrap(),
            &hosts
        ));
        assert!(!model_url_is_allowed(
            &url::Url::parse("https://user@huggingface.co/model").unwrap(),
            &hosts
        ));
    }

    #[test]
    fn removing_models_only_cleans_scoped_partial_cache_files() {
        let folder = tempfile::tempdir().unwrap();
        let partial = folder
            .path()
            .join("model-gemma-4-e2b-0123456789abcdef.svmodel.partial");
        let unrelated = folder.path().join("important.partial");
        let nested = folder.path().join("model-nested.svmodel.partial");
        fs::write(&partial, b"partial").unwrap();
        fs::write(&unrelated, b"keep").unwrap();
        fs::create_dir(&nested).unwrap();

        clean_model_partial_cache(folder.path()).unwrap();

        assert!(!partial.exists());
        assert!(unrelated.exists());
        assert!(nested.is_dir());
    }

    #[test]
    fn cancellation_targets_only_the_selected_model_generation() {
        let generation = AtomicU64::new(0);
        let cancelled_generation = AtomicU64::new(0);
        let first = next_model_generation(&generation);
        assert!(!model_operation_cancelled(&cancelled_generation, first));
        cancelled_generation.store(first, Ordering::Release);
        assert!(model_operation_cancelled(&cancelled_generation, first));
        let second = next_model_generation(&generation);
        assert!(!model_operation_cancelled(&cancelled_generation, second));
    }
}
