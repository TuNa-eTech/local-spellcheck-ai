use crate::error::{AppError, AppResult};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    thread,
    time::Duration,
};

const LOCAL_GEMMA_REVIEW_CONTEXT_TOKENS: u64 = 4096;
const LOCAL_REVIEW_TIMEOUT_SECONDS: u64 = 600;
pub(crate) const MAX_MODEL_TIMEOUT_SECONDS: u64 = 900;
const _: () = assert!(LOCAL_REVIEW_TIMEOUT_SECONDS <= MAX_MODEL_TIMEOUT_SECONDS);

/// On Windows, antivirus scanning a freshly written multi-GB `.gguf`, or the
/// engine still holding a memory-map on the model file it just closed, can lock
/// a path for a fraction of a second — surfacing as os error 5 (access denied)
/// or 32 (sharing violation). A short bounded backoff clears essentially all of
/// these; a genuine permission error still propagates after the last attempt.
fn is_transient_fs_error(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(5) | Some(32))
}

fn with_fs_retry<T>(mut op: impl FnMut() -> io::Result<T>) -> io::Result<T> {
    let mut delay = Duration::from_millis(40);
    for _ in 0..7 {
        match op() {
            Err(error) if is_transient_fs_error(&error) => {
                thread::sleep(delay);
                delay = (delay * 2).min(Duration::from_millis(750));
            }
            other => return other,
        }
    }
    op()
}

fn rename_retrying(from: &Path, to: &Path) -> io::Result<()> {
    with_fs_retry(|| fs::rename(from, to))
}

fn remove_dir_all_retrying(path: &Path) -> io::Result<()> {
    with_fs_retry(|| fs::remove_dir_all(path))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelStatus {
    pub state: String,
    pub model_id: Option<String>,
    pub version: Option<String>,
    pub code: Option<String>,
    #[serde(default)]
    pub trust: Option<ModelTrust>,
    #[serde(default)]
    pub release_approved: bool,
    #[serde(default)]
    pub capabilities: ModelCapabilities,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelTrust {
    ReleaseSigned,
    LocalUnverified,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelCapabilities {
    #[serde(default)]
    pub candidate_filter: bool,
    #[serde(default)]
    pub full_review: bool,
}

#[derive(Debug, Deserialize, Serialize)]
struct Manifest {
    schema_version: u8,
    model_id: String,
    version: String,
    engine_protocol: u8,
    file: String,
    size: u64,
    sha256: String,
    license_file: String,
    memory_mb: Option<u64>,
    context_size: Option<u64>,
    batch_size: Option<u64>,
    max_tokens: Option<u64>,
    review_chunk_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    review_mode: Option<String>,
    timeout_seconds: Option<u64>,
    seed: Option<i64>,
    minimum_confidence: Option<f64>,
    trust: ModelTrust,
    capabilities: ModelCapabilities,
    #[serde(skip_serializing_if = "Option::is_none")]
    quality_gate: Option<QualityGate>,
    #[serde(skip_serializing_if = "Option::is_none")]
    signature: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct QualityGate {
    corpus_sha256: String,
    profiles: Vec<BenchmarkProfile>,
}

#[derive(Debug, Deserialize, Serialize)]
struct BenchmarkProfile {
    machine_memory_mb: f64,
    documents: u64,
    precision: f64,
    recall: f64,
    p95_seconds: f64,
    peak_rss_mb: f64,
    report_sha256: String,
}

pub struct ModelProvisioner {
    root: PathBuf,
}

impl ModelProvisioner {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }
    pub fn recover_interrupted_activation(&self) -> AppResult<()> {
        fs::create_dir_all(&self.root)?;
        let active = self.root.join("active");
        let previous = self.root.join("previous");
        if !active.exists() && previous.exists() {
            rename_retrying(&previous, &active)?;
        }
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            if entry.file_type()?.is_dir()
                && entry.file_name().to_string_lossy().starts_with("staging-")
            {
                remove_dir_all_retrying(&entry.path())?;
            }
        }
        self.migrate_local_experimental_review_defaults()?;
        Ok(())
    }

    fn migrate_local_experimental_review_defaults(&self) -> AppResult<()> {
        let manifest_path = self.root.join("active/manifest.json");
        if !manifest_path.is_file() {
            return Ok(());
        }
        let Some(mut manifest) = fs::read_to_string(&manifest_path)
            .ok()
            .and_then(|value| serde_json::from_str::<Manifest>(&value).ok())
        else {
            return Ok(());
        };
        let eligible = manifest.schema_version == 2
            && manifest.trust == ModelTrust::LocalUnverified
            && manifest.capabilities.candidate_filter
            && manifest.quality_gate.is_none()
            && manifest.signature.is_none();
        let mut changed = false;
        if eligible && !manifest.capabilities.full_review {
            manifest.capabilities.full_review = true;
            changed = true;
        }
        if eligible
            && manifest
                .review_chunk_tokens
                .map_or(true, |tokens| tokens > 500)
        {
            manifest.review_chunk_tokens = Some(500);
            changed = true;
        }
        if eligible && manifest.review_mode.as_deref() != Some("lightweight") {
            manifest.review_mode = Some("lightweight".to_string());
            changed = true;
        }
        if eligible
            && manifest
                .timeout_seconds
                .map_or(true, |seconds| seconds < LOCAL_REVIEW_TIMEOUT_SECONDS)
        {
            manifest.timeout_seconds = Some(LOCAL_REVIEW_TIMEOUT_SECONDS);
            changed = true;
        }
        let local_context_size = local_review_context_size(&manifest.model_id);
        if eligible
            && manifest
                .context_size
                .map_or(true, |tokens| tokens < local_context_size)
        {
            manifest.context_size = Some(local_context_size);
            changed = true;
        }
        if changed {
            fs::write(&manifest_path, serde_json::to_vec_pretty(&manifest)?)?;
        }
        Ok(())
    }
    pub fn has_pending_activation(&self) -> bool {
        self.root.join("previous").exists()
    }
    pub fn status(&self) -> ModelStatus {
        let manifest = self.root.join("active/manifest.json");
        if !manifest.exists() {
            return ModelStatus {
                state: "not_installed".into(),
                model_id: None,
                version: None,
                code: None,
                trust: None,
                release_approved: false,
                capabilities: ModelCapabilities::default(),
            };
        }
        match fs::read_to_string(manifest)
            .ok()
            .and_then(|value| serde_json::from_str::<Manifest>(&value).ok())
        {
            Some(value)
                if valid_trust_contract(&value)
                    && (value.trust == ModelTrust::LocalUnverified
                        || release_signature_is_valid(&value)) =>
            {
                status_for_manifest("installed", value)
            }
            None => ModelStatus {
                state: "invalid".into(),
                model_id: None,
                version: None,
                code: Some("MODEL_MANIFEST_INVALID".into()),
                trust: None,
                release_approved: false,
                capabilities: ModelCapabilities::default(),
            },
            Some(_) => ModelStatus {
                state: "invalid".into(),
                model_id: None,
                version: None,
                code: Some("MODEL_MANIFEST_INVALID".into()),
                trust: None,
                release_approved: false,
                capabilities: ModelCapabilities::default(),
            },
        }
    }
    pub fn import_with_cancel<F>(
        &self,
        package: &Path,
        expected_model_id: Option<&str>,
        cancelled: F,
    ) -> AppResult<ModelStatus>
    where
        F: Fn() -> bool,
    {
        let staging = self.root.join(format!("staging-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&staging)?;
        let result = self
            .verify_and_extract(package, &staging, expected_model_id, &cancelled)
            .and_then(|manifest| {
                let active = self.root.join("active");
                let backup = self.root.join("previous");
                if backup.exists() {
                    remove_dir_all_retrying(&backup)?;
                }
                if active.exists() {
                    rename_retrying(&active, &backup)?;
                }
                if let Err(error) = rename_retrying(&staging, &active) {
                    if backup.exists() {
                        let _ = rename_retrying(&backup, &active);
                    }
                    return Err(error.into());
                }
                Ok(status_for_manifest("installed", manifest))
            });
        if staging.exists() {
            let _ = fs::remove_dir_all(staging);
        }
        result
    }
    fn verify_and_extract<F>(
        &self,
        package: &Path,
        staging: &Path,
        expected_model_id: Option<&str>,
        cancelled: &F,
    ) -> AppResult<Manifest>
    where
        F: Fn() -> bool,
    {
        if cancelled() {
            return Err(AppError::ModelCancelled);
        }
        let mut check_file = fs::File::open(package)?;
        let mut magic = [0u8; 4];
        let is_gguf = check_file.read_exact(&mut magic).is_ok() && &magic == b"GGUF";
        if is_gguf {
            return self.extract_raw_gguf(package, staging, expected_model_id, cancelled);
        }
        let file = fs::File::open(package)?;
        let mut archive = zip::ZipArchive::new(file).map_err(|_| AppError::ModelPackageInvalid)?;
        let names: Vec<String> = archive.file_names().map(str::to_owned).collect();
        let unique: HashSet<&str> = names.iter().map(String::as_str).collect();
        if names.len() > 16
            || unique.len() != names.len()
            || names.iter().any(|name| !safe_package_name(name))
        {
            return Err(AppError::ModelPackageInvalid);
        }
        let manifest_bytes = read_entry(&mut archive, "manifest.json", 64 * 1024)?;
        let manifest: Manifest = serde_json::from_slice(&manifest_bytes)?;
        let expected_names: HashSet<&str> = [
            "manifest.json",
            manifest.file.as_str(),
            manifest.license_file.as_str(),
        ]
        .into_iter()
        .collect();
        if manifest.schema_version != 2
            || manifest.engine_protocol != 1
            || !safe_package_name(&manifest.file)
            || !safe_package_name(&manifest.license_file)
            || manifest.file == manifest.license_file
            || unique != expected_names
            || !valid_model_id(&manifest.model_id)
            || manifest.version.trim().is_empty()
            || manifest.size == 0
            || manifest.sha256.len() != 64
            || !manifest
                .sha256
                .bytes()
                .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
            || manifest.memory_mb == Some(0)
            || manifest
                .context_size
                .is_some_and(|value| !(512..=32_768).contains(&value))
            || manifest
                .batch_size
                .is_some_and(|value| !(1..=64).contains(&value))
            || manifest
                .max_tokens
                .is_some_and(|value| !(32..=4096).contains(&value))
            || manifest
                .review_chunk_tokens
                .is_some_and(|value| !(64..=32_768).contains(&value))
            || !valid_runtime_window(manifest.context_size, manifest.max_tokens)
            || !valid_timeout_seconds(manifest.timeout_seconds)
            || manifest
                .minimum_confidence
                .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
            || !valid_trust_contract(&manifest)
            || expected_model_id.is_some_and(|expected| manifest.model_id != expected)
        {
            return Err(AppError::ModelPackageInvalid);
        }
        if manifest.trust != ModelTrust::ReleaseSigned {
            return Err(AppError::ModelPackageInvalid);
        }
        let public_key =
            option_env!("SOATVAN_MODEL_PUBLIC_KEY").ok_or(AppError::ModelNotConfigured)?;
        let key_bytes: [u8; 32] = STANDARD
            .decode(public_key)
            .map_err(|_| AppError::ModelNotConfigured)?
            .try_into()
            .map_err(|_| AppError::ModelNotConfigured)?;
        let signature = Signature::from_slice(
            &STANDARD
                .decode(
                    manifest
                        .signature
                        .as_deref()
                        .ok_or(AppError::ModelSignatureInvalid)?,
                )
                .map_err(|_| AppError::ModelSignatureInvalid)?,
        )
        .map_err(|_| AppError::ModelSignatureInvalid)?;
        let unsigned = canonical_unsigned(&manifest)?;
        VerifyingKey::from_bytes(&key_bytes)
            .map_err(|_| AppError::ModelNotConfigured)?
            .verify(&unsigned, &signature)
            .map_err(|_| AppError::ModelSignatureInvalid)?;
        extract_model(&mut archive, &manifest, staging, cancelled)?;
        if cancelled() {
            return Err(AppError::ModelCancelled);
        }
        let license = read_entry(&mut archive, &manifest.license_file, 1024 * 1024)?;
        if license.is_empty() {
            return Err(AppError::ModelPackageInvalid);
        }
        fs::write(staging.join(&manifest.license_file), license)?;
        fs::write(staging.join("manifest.json"), manifest_bytes)?;
        Ok(manifest)
    }

    fn extract_raw_gguf<F>(
        &self,
        gguf_path: &Path,
        staging: &Path,
        expected_model_id: Option<&str>,
        cancelled: &F,
    ) -> AppResult<Manifest>
    where
        F: Fn() -> bool,
    {
        let file_stem = gguf_path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("gemma-4-e4b");
        let model_id = expected_model_id
            .map(str::to_owned)
            .unwrap_or_else(|| local_model_id(file_stem));
        eprintln!("[soatvan-host] extract_raw_gguf: path={gguf_path:?}, file_stem={file_stem:?}, model_id={model_id:?}");
        if !valid_model_id(&model_id) {
            eprintln!("[soatvan-host] extract_raw_gguf: invalid model_id → ModelPackageInvalid");
            return Err(AppError::ModelPackageInvalid);
        }
        let file_name = "model.gguf";
        let target_path = staging.join(file_name);

        let metadata = fs::metadata(gguf_path)?;
        let size = metadata.len();
        let available = fs2::available_space(staging)?;
        const DISK_MARGIN: u64 = 64 * 1024 * 1024;
        eprintln!("[soatvan-host] extract_raw_gguf: size={size} bytes, available_space={available} bytes");
        if size == 0 || available < size.saturating_add(DISK_MARGIN) {
            eprintln!("[soatvan-host] extract_raw_gguf: insufficient disk space → ModelDiskSpace");
            return Err(AppError::ModelDiskSpace);
        }

        let mut source = fs::File::open(gguf_path)?;
        let partial_path = staging.join(format!("{file_name}.partial"));
        let mut target = fs::File::create(&partial_path)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 1024 * 1024];
        let mut copied = 0_u64;
        loop {
            if cancelled() {
                return Err(AppError::ModelCancelled);
            }
            let count = source.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            copied = copied.saturating_add(count as u64);
            if copied > size {
                return Err(AppError::ModelPackageInvalid);
            }
            hasher.update(&buffer[..count]);
            target.write_all(&buffer[..count])?;
        }
        if copied != size {
            return Err(AppError::ModelPackageInvalid);
        }
        target.flush()?;
        target.sync_all()?;
        let sha256 = format!("{:x}", hasher.finalize());
        rename_retrying(&partial_path, &target_path)?;

        let license_name = "LOCAL-IMPORT-NOTICE.txt";
        fs::write(
            staging.join(license_name),
            b"Locally imported GGUF. No license or release approval was supplied.\n",
        )?;

        let context_size = local_review_context_size(&model_id);
        let manifest = Manifest {
            schema_version: 2,
            model_id,
            version: "local".to_string(),
            engine_protocol: 1,
            file: file_name.to_string(),
            size,
            sha256,
            license_file: license_name.to_string(),
            memory_mb: None,
            context_size: Some(context_size),
            batch_size: Some(8),
            max_tokens: Some(512),
            review_chunk_tokens: Some(500),
            review_mode: Some("lightweight".to_string()),
            timeout_seconds: Some(LOCAL_REVIEW_TIMEOUT_SECONDS),
            seed: Some(42),
            minimum_confidence: Some(0.8),
            trust: ModelTrust::LocalUnverified,
            capabilities: ModelCapabilities {
                candidate_filter: true,
                full_review: true,
            },
            quality_gate: None,
            signature: None,
        };

        let manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
        fs::write(staging.join("manifest.json"), manifest_bytes)?;
        eprintln!(
            "[soatvan-host] extract_raw_gguf: DONE — model_id={:?}, sha256={}, size={size}",
            manifest.model_id,
            manifest.sha256,
        );
        Ok(manifest)
    }
    pub fn commit_activation(&self) -> AppResult<()> {
        let backup = self.root.join("previous");
        if backup.exists() {
            remove_dir_all_retrying(&backup)?;
        }
        Ok(())
    }
    pub fn rollback_activation(&self) -> AppResult<()> {
        let active = self.root.join("active");
        let backup = self.root.join("previous");
        if active.exists() {
            remove_dir_all_retrying(&active)?;
        }
        if backup.exists() {
            rename_retrying(&backup, &active)?;
        }
        Ok(())
    }
    pub fn remove(&self) -> AppResult<ModelStatus> {
        let active = self.root.join("active");
        if active.exists() {
            remove_dir_all_retrying(&active)?;
        }
        Ok(self.status())
    }
}

fn read_entry(
    archive: &mut zip::ZipArchive<fs::File>,
    name: &str,
    maximum: u64,
) -> AppResult<Vec<u8>> {
    let entry = archive
        .by_name(name)
        .map_err(|_| AppError::ModelPackageInvalid)?;
    if entry.size() > maximum {
        return Err(AppError::ModelPackageInvalid);
    }
    let mut data = Vec::with_capacity(entry.size() as usize);
    entry.take(maximum).read_to_end(&mut data)?;
    Ok(data)
}

fn extract_model<F>(
    archive: &mut zip::ZipArchive<fs::File>,
    manifest: &Manifest,
    staging: &Path,
    cancelled: &F,
) -> AppResult<()>
where
    F: Fn() -> bool,
{
    const DISK_MARGIN: u64 = 64 * 1024 * 1024;
    if fs2::available_space(staging)? < manifest.size.saturating_add(DISK_MARGIN) {
        return Err(AppError::ModelDiskSpace);
    }
    let mut entry = archive
        .by_name(&manifest.file)
        .map_err(|_| AppError::ModelPackageInvalid)?;
    if entry.size() != manifest.size {
        return Err(AppError::ModelPackageInvalid);
    }
    let partial = staging.join(format!("{}.partial", manifest.file));
    let final_path = staging.join(&manifest.file);
    let mut output = fs::File::create(&partial)?;
    let mut digest = Sha256::new();
    let mut copied = 0_u64;
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        if cancelled() {
            return Err(AppError::ModelCancelled);
        }
        let count = entry.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        copied = copied.saturating_add(count as u64);
        if copied > manifest.size {
            return Err(AppError::ModelPackageInvalid);
        }
        digest.update(&buffer[..count]);
        output.write_all(&buffer[..count])?;
    }
    output.flush()?;
    output.sync_all()?;
    if copied != manifest.size || format!("{:x}", digest.finalize()) != manifest.sha256 {
        return Err(AppError::ModelPackageInvalid);
    }
    rename_retrying(&partial, &final_path)?;
    Ok(())
}
fn canonical_unsigned(value: &Manifest) -> AppResult<Vec<u8>> {
    let mut unsigned = serde_json::json!({
        "schema_version": value.schema_version,
        "model_id": value.model_id,
        "version": value.version,
        "engine_protocol": value.engine_protocol,
        "file": value.file,
        "size": value.size,
        "sha256": value.sha256,
        "license_file": value.license_file,
        "trust": value.trust,
        "capabilities": value.capabilities,
    });
    let object = unsigned.as_object_mut().expect("canonical manifest object");
    for (name, item) in [
        ("memory_mb", value.memory_mb.map(serde_json::Value::from)),
        (
            "context_size",
            value.context_size.map(serde_json::Value::from),
        ),
        ("batch_size", value.batch_size.map(serde_json::Value::from)),
        ("max_tokens", value.max_tokens.map(serde_json::Value::from)),
        (
            "review_chunk_tokens",
            value.review_chunk_tokens.map(serde_json::Value::from),
        ),
        (
            "review_mode",
            value.review_mode.as_ref().map(|s| serde_json::Value::from(s.clone())),
        ),
        (
            "timeout_seconds",
            value.timeout_seconds.map(serde_json::Value::from),
        ),
        ("seed", value.seed.map(serde_json::Value::from)),
        (
            "minimum_confidence",
            value.minimum_confidence.map(serde_json::Value::from),
        ),
    ] {
        if let Some(item) = item {
            object.insert(name.into(), item);
        }
    }
    if let Some(quality_gate) = value.quality_gate.as_ref() {
        object.insert("quality_gate".into(), serde_json::to_value(quality_gate)?);
    }
    Ok(serde_json::to_vec(&unsigned)?)
}

fn status_for_manifest(state: &str, manifest: Manifest) -> ModelStatus {
    let release_approved = manifest.trust == ModelTrust::ReleaseSigned;
    ModelStatus {
        state: state.into(),
        model_id: Some(manifest.model_id),
        version: Some(manifest.version),
        code: None,
        trust: Some(manifest.trust),
        release_approved,
        capabilities: manifest.capabilities,
    }
}

fn valid_trust_contract(manifest: &Manifest) -> bool {
    match manifest.trust {
        ModelTrust::ReleaseSigned => {
            manifest.capabilities.candidate_filter
                && manifest
                    .quality_gate
                    .as_ref()
                    .is_some_and(valid_quality_gate)
                && manifest
                    .signature
                    .as_ref()
                    .is_some_and(|signature| signature.len() >= 32)
        }
        ModelTrust::LocalUnverified => {
            manifest.capabilities.candidate_filter
                && manifest.quality_gate.is_none()
                && manifest.signature.is_none()
        }
    }
}

fn release_signature_is_valid(manifest: &Manifest) -> bool {
    let Some(public_key) = option_env!("SOATVAN_MODEL_PUBLIC_KEY") else {
        return false;
    };
    let Ok(key_bytes) = STANDARD.decode(public_key) else {
        return false;
    };
    let Ok(key_bytes) = <[u8; 32]>::try_from(key_bytes) else {
        return false;
    };
    let Some(encoded_signature) = manifest.signature.as_deref() else {
        return false;
    };
    let Ok(signature_bytes) = STANDARD.decode(encoded_signature) else {
        return false;
    };
    let Ok(signature) = Signature::from_slice(&signature_bytes) else {
        return false;
    };
    let Ok(payload) = canonical_unsigned(manifest) else {
        return false;
    };
    VerifyingKey::from_bytes(&key_bytes)
        .and_then(|key| key.verify(&payload, &signature))
        .is_ok()
}

fn safe_package_name(value: &str) -> bool {
    !value.is_empty() && value != "." && !value.contains('/') && !value.contains('\\')
}

fn valid_model_id(value: &str) -> bool {
    value.len() >= 2
        && value
            .bytes()
            .next()
            .is_some_and(|item| item.is_ascii_lowercase() || item.is_ascii_digit())
        && value.bytes().all(|item| {
            item.is_ascii_lowercase() || item.is_ascii_digit() || matches!(item, b'.' | b'_' | b'-')
        })
}

fn valid_timeout_seconds(value: Option<u64>) -> bool {
    match value {
        Some(seconds) => (1..=MAX_MODEL_TIMEOUT_SECONDS).contains(&seconds),
        None => true,
    }
}

fn local_model_id(file_stem: &str) -> String {
    let lower = file_stem.to_ascii_lowercase();
    if lower.contains("e2b") {
        return "gemma-4-e2b".into();
    }
    if lower.contains("e4b") {
        return "gemma-4-e4b".into();
    }
    if lower.contains("12b") {
        return "gemma-4-12b".into();
    }
    let mut previous_separator = false;
    let slug: String = lower
        .bytes()
        .filter_map(|byte| {
            let output = if byte.is_ascii_lowercase() || byte.is_ascii_digit() {
                previous_separator = false;
                byte as char
            } else if !previous_separator {
                previous_separator = true;
                '-'
            } else {
                return None;
            };
            Some(output)
        })
        .take(48)
        .collect();
    let slug = slug.trim_matches('-');
    format!("local-{}", if slug.is_empty() { "gguf" } else { slug })
}

fn valid_runtime_window(context_size: Option<u64>, max_tokens: Option<u64>) -> bool {
    let available = context_size
        .unwrap_or(2048)
        .saturating_sub(max_tokens.unwrap_or(512).saturating_add(256));
    available >= 64
}

fn local_review_context_size(model_id: &str) -> u64 {
    if model_id.starts_with("gemma-4-") {
        LOCAL_GEMMA_REVIEW_CONTEXT_TOKENS
    } else {
        2048
    }
}

fn valid_quality_gate(value: &QualityGate) -> bool {
    let valid_hash = |hash: &str| {
        hash.len() == 64
            && hash
                .bytes()
                .all(|item| item.is_ascii_digit() || (b'a'..=b'f').contains(&item))
    };
    let valid_profiles = value.profiles.len() >= 2
        && value.profiles.iter().all(|profile| {
            profile.machine_memory_mb.is_finite()
                && profile.machine_memory_mb >= 7000.0
                && profile.documents >= 20
                && profile.precision.is_finite()
                && (0.90..=1.0).contains(&profile.precision)
                && profile.recall.is_finite()
                && (0.85..=1.0).contains(&profile.recall)
                && profile.p95_seconds.is_finite()
                && (0.0..=180.0).contains(&profile.p95_seconds)
                && profile.p95_seconds > 0.0
                && profile.peak_rss_mb.is_finite()
                && profile.peak_rss_mb > 0.0
                && valid_hash(&profile.report_sha256)
        });
    valid_hash(&value.corpus_sha256)
        && valid_profiles
        && value
            .profiles
            .iter()
            .any(|profile| (7000.0..=9216.0).contains(&profile.machine_memory_mb))
        && value
            .profiles
            .iter()
            .any(|profile| (15_000.0..=18_432.0).contains(&profile.machine_memory_mb))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest_for(bytes: &[u8]) -> Manifest {
        Manifest {
            schema_version: 2,
            model_id: "approved-model".into(),
            version: "1.0.0".into(),
            engine_protocol: 1,
            file: "model.gguf".into(),
            size: bytes.len() as u64,
            sha256: format!("{:x}", Sha256::digest(bytes)),
            license_file: "LICENSE.txt".into(),
            memory_mb: Some(2048),
            context_size: Some(2048),
            batch_size: Some(8),
            max_tokens: Some(512),
            review_chunk_tokens: Some(1200),
            review_mode: None,
            timeout_seconds: Some(120),
            seed: Some(42),
            minimum_confidence: Some(0.8),
            trust: ModelTrust::ReleaseSigned,
            capabilities: ModelCapabilities {
                candidate_filter: true,
                full_review: false,
            },
            quality_gate: Some(QualityGate {
                corpus_sha256: "c".repeat(64),
                profiles: vec![
                    BenchmarkProfile {
                        machine_memory_mb: 8192.0,
                        documents: 20,
                        precision: 0.91,
                        recall: 0.86,
                        p95_seconds: 2.0,
                        peak_rss_mb: 2048.0,
                        report_sha256: "a".repeat(64),
                    },
                    BenchmarkProfile {
                        machine_memory_mb: 16384.0,
                        documents: 20,
                        precision: 0.92,
                        recall: 0.87,
                        p95_seconds: 1.0,
                        peak_rss_mb: 2048.0,
                        report_sha256: "b".repeat(64),
                    },
                ],
            }),
            signature: Some("unused-in-unit-test".into()),
        }
    }

    #[test]
    fn model_identifiers_and_package_names_are_strict() {
        assert!(valid_model_id("gemma-3.q4"));
        assert!(!valid_model_id("Gemma"));
        assert!(!valid_model_id("../model"));
        assert!(safe_package_name("model.gguf"));
        assert!(!safe_package_name("folder/model.gguf"));
        assert_eq!(local_model_id("gemma-4-E2B-it-Q4"), "gemma-4-e2b");
        assert_eq!(local_model_id("My Custom Model"), "local-my-custom-model");
        assert_eq!(local_model_id("má»™t-model"), "local-m-t-model");
    }

    #[test]
    fn model_quality_and_runtime_deadlines_are_bounded_by_m2_sla() {
        let mut manifest = manifest_for(b"model");
        assert!(valid_quality_gate(
            manifest.quality_gate.as_ref().expect("quality gate")
        ));
        manifest
            .quality_gate
            .as_mut()
            .expect("quality gate")
            .profiles[0]
            .p95_seconds = 181.0;
        assert!(!valid_quality_gate(
            manifest.quality_gate.as_ref().expect("quality gate")
        ));
        assert!(valid_timeout_seconds(Some(MAX_MODEL_TIMEOUT_SECONDS)));
        assert!(!valid_timeout_seconds(Some(MAX_MODEL_TIMEOUT_SECONDS + 1)));
        assert!(valid_runtime_window(Some(2048), Some(512)));
        assert!(!valid_runtime_window(Some(512), Some(512)));
        assert!(valid_runtime_window(Some(1024), Some(512)));
        manifest.review_chunk_tokens = Some(63);
        assert!(manifest
            .review_chunk_tokens
            .is_some_and(|value| !(64..=32_768).contains(&value)));
    }

    #[test]
    fn model_extraction_streams_and_verifies_digest() {
        let folder = tempfile::tempdir().unwrap();
        let archive_path = folder.path().join("model.svmodel");
        let bytes = vec![7_u8; 2 * 1024 * 1024];
        let file = fs::File::create(&archive_path).unwrap();
        let mut archive = zip::ZipWriter::new(file);
        archive
            .start_file("model.gguf", zip::write::SimpleFileOptions::default())
            .unwrap();
        archive.write_all(&bytes).unwrap();
        archive.finish().unwrap();

        let file = fs::File::open(&archive_path).unwrap();
        let mut archive = zip::ZipArchive::new(file).unwrap();
        let staging = folder.path().join("staging");
        fs::create_dir(&staging).unwrap();
        extract_model(&mut archive, &manifest_for(&bytes), &staging, &|| false).unwrap();
        assert_eq!(fs::read(staging.join("model.gguf")).unwrap(), bytes);
        assert!(!staging.join("model.gguf.partial").exists());
    }

    #[test]
    fn model_extraction_can_be_cancelled_between_chunks() {
        use std::cell::Cell;

        let folder = tempfile::tempdir().unwrap();
        let archive_path = folder.path().join("model.svmodel");
        let bytes = vec![9_u8; 3 * 1024 * 1024];
        let file = fs::File::create(&archive_path).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        writer
            .start_file("model.gguf", zip::write::SimpleFileOptions::default())
            .unwrap();
        writer.write_all(&bytes).unwrap();
        writer.finish().unwrap();
        let file = fs::File::open(&archive_path).unwrap();
        let mut archive = zip::ZipArchive::new(file).unwrap();
        let staging = folder.path().join("staging");
        fs::create_dir(&staging).unwrap();
        let calls = Cell::new(0);
        let result = extract_model(&mut archive, &manifest_for(&bytes), &staging, &|| {
            calls.set(calls.get() + 1);
            calls.get() > 2
        });
        assert!(matches!(result, Err(AppError::ModelCancelled)));
        assert!(!staging.join("model.gguf").exists());
    }

    #[test]
    fn signature_payload_matches_sorted_compact_json_contract() {
        let manifest = manifest_for(b"model");
        let payload = String::from_utf8(canonical_unsigned(&manifest).unwrap()).unwrap();
        assert!(!payload.contains("signature"));
        assert!(payload.starts_with(
            "{\"batch_size\":8,\"capabilities\":{\"candidate_filter\":true,\"full_review\":false},\"context_size\":2048"
        ));
        assert!(payload.contains("\"quality_gate\":{\"corpus_sha256\":"));
        assert!(payload.contains("\"review_chunk_tokens\":1200"));
        assert!(payload.contains("\"trust\":\"release_signed\""));
        assert!(payload.contains("\"full_review\":false"));
        assert!(payload.ends_with("\"version\":\"1.0.0\"}"));
    }

    #[test]
    fn local_import_exposes_experimental_full_review_without_release_approval() {
        let mut manifest = manifest_for(b"model");
        manifest.trust = ModelTrust::LocalUnverified;
        manifest.capabilities.full_review = true;
        manifest.quality_gate = None;
        manifest.signature = None;

        assert!(valid_trust_contract(&manifest));
        let status = status_for_manifest("ready", manifest);
        assert_eq!(status.trust, Some(ModelTrust::LocalUnverified));
        assert!(!status.release_approved);
        assert!(status.capabilities.candidate_filter);
        assert!(status.capabilities.full_review);
    }

    #[test]
    fn unverified_manifest_cannot_claim_fake_release_evidence() {
        let mut manifest = manifest_for(b"model");
        manifest.trust = ModelTrust::LocalUnverified;
        manifest.quality_gate = None;
        manifest.signature = None;
        manifest.capabilities.full_review = true;
        assert!(valid_trust_contract(&manifest));

        manifest.quality_gate = Some(QualityGate {
            corpus_sha256: "0".repeat(64),
            profiles: Vec::new(),
        });
        assert!(!valid_trust_contract(&manifest));
    }

    #[test]
    fn raw_gguf_import_records_provenance_without_fabricated_approval() {
        let folder = tempfile::tempdir().unwrap();
        let source = folder.path().join("download-cache.partial");
        fs::write(&source, b"GGUF-local-model").unwrap();
        let staging = folder.path().join("staging");
        fs::create_dir(&staging).unwrap();
        let provisioner = ModelProvisioner::new(folder.path().join("models"));

        let manifest = provisioner
            .extract_raw_gguf(&source, &staging, Some("gemma-4-e2b"), &|| false)
            .unwrap();

        assert_eq!(manifest.model_id, "gemma-4-e2b");
        assert_eq!(manifest.version, "local");
        assert_eq!(manifest.trust, ModelTrust::LocalUnverified);
        assert_eq!(manifest.context_size, Some(4096));
        assert_eq!(manifest.timeout_seconds, Some(600));
        assert!(manifest.capabilities.candidate_filter);
        assert!(manifest.capabilities.full_review);
        assert!(manifest.quality_gate.is_none());
        assert!(manifest.signature.is_none());
        let stored: serde_json::Value = serde_json::from_slice(
            &fs::read(staging.join("manifest.json")).expect("stored manifest"),
        )
        .unwrap();
        assert!(stored.get("quality_gate").is_none());
        assert!(stored.get("signature").is_none());
        assert!(staging.join("LOCAL-IMPORT-NOTICE.txt").is_file());
    }

    #[test]
    fn activation_rollback_restores_previous_model() {
        let folder = tempfile::tempdir().unwrap();
        let root = folder.path().join("models");
        fs::create_dir_all(root.join("active")).unwrap();
        fs::create_dir_all(root.join("previous")).unwrap();
        fs::write(root.join("active/new"), b"new").unwrap();
        fs::write(root.join("previous/old"), b"old").unwrap();
        ModelProvisioner::new(root.clone())
            .rollback_activation()
            .unwrap();
        assert!(!root.join("active/new").exists());
        assert_eq!(fs::read(root.join("active/old")).unwrap(), b"old");
        assert!(!root.join("previous").exists());
    }

    #[test]
    fn startup_recovery_restores_previous_model_and_removes_staging() {
        let folder = tempfile::tempdir().unwrap();
        let root = folder.path().join("models");
        fs::create_dir_all(root.join("previous")).unwrap();
        fs::create_dir_all(root.join("staging-interrupted")).unwrap();
        fs::write(root.join("previous/model.gguf"), b"old").unwrap();
        let provisioner = ModelProvisioner::new(root.clone());
        provisioner.recover_interrupted_activation().unwrap();
        assert_eq!(fs::read(root.join("active/model.gguf")).unwrap(), b"old");
        assert!(!root.join("previous").exists());
        assert!(!root.join("staging-interrupted").exists());
    }

    #[test]
    fn startup_recovery_migrates_existing_local_model_to_experimental_review_defaults() {
        let folder = tempfile::tempdir().unwrap();
        let root = folder.path().join("models");
        fs::create_dir_all(root.join("active")).unwrap();
        let mut manifest = manifest_for(b"model");
        manifest.model_id = "gemma-4-e2b".into();
        manifest.trust = ModelTrust::LocalUnverified;
        manifest.capabilities.full_review = false;
        manifest.quality_gate = None;
        manifest.signature = None;
        fs::write(
            root.join("active/manifest.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();

        ModelProvisioner::new(root.clone())
            .recover_interrupted_activation()
            .unwrap();

        let migrated: Manifest =
            serde_json::from_slice(&fs::read(root.join("active/manifest.json")).unwrap()).unwrap();
        assert_eq!(migrated.trust, ModelTrust::LocalUnverified);
        assert!(migrated.capabilities.full_review);
        assert_eq!(migrated.context_size, Some(4096));
        assert_eq!(migrated.review_chunk_tokens, Some(500));
        assert_eq!(migrated.review_mode.as_deref(), Some("lightweight"));
        assert_eq!(migrated.timeout_seconds, Some(600));
        assert!(migrated.quality_gate.is_none());
        assert!(migrated.signature.is_none());
    }
}
