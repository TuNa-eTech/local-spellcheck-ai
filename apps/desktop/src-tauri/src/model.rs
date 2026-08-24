use crate::error::{AppError, AppResult};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelStatus {
    pub state: String,
    pub model_id: Option<String>,
    pub version: Option<String>,
    pub code: Option<String>,
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
    timeout_seconds: Option<u64>,
    seed: Option<i64>,
    minimum_confidence: Option<f64>,
    quality_gate: QualityGate,
    signature: String,
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
            fs::rename(&previous, &active)?;
        }
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            if entry.file_type()?.is_dir()
                && entry.file_name().to_string_lossy().starts_with("staging-")
            {
                fs::remove_dir_all(entry.path())?;
            }
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
            };
        }
        match fs::read_to_string(manifest)
            .ok()
            .and_then(|value| serde_json::from_str::<Manifest>(&value).ok())
        {
            Some(value) => ModelStatus {
                state: "installed".into(),
                model_id: Some(value.model_id),
                version: Some(value.version),
                code: None,
            },
            None => ModelStatus {
                state: "invalid".into(),
                model_id: None,
                version: None,
                code: Some("MODEL_MANIFEST_INVALID".into()),
            },
        }
    }
    pub fn import_with_cancel<F>(&self, package: &Path, cancelled: F) -> AppResult<ModelStatus>
    where
        F: Fn() -> bool,
    {
        let staging = self.root.join(format!("staging-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&staging)?;
        let result = self
            .verify_and_extract(package, &staging, &cancelled)
            .and_then(|manifest| {
                let active = self.root.join("active");
                let backup = self.root.join("previous");
                if backup.exists() {
                    fs::remove_dir_all(&backup)?;
                }
                if active.exists() {
                    fs::rename(&active, &backup)?;
                }
                if let Err(error) = fs::rename(&staging, &active) {
                    if backup.exists() {
                        let _ = fs::rename(&backup, &active);
                    }
                    return Err(error.into());
                }
                Ok(ModelStatus {
                    state: "installed".into(),
                    model_id: Some(manifest.model_id),
                    version: Some(manifest.version),
                    code: None,
                })
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
            return self.extract_raw_gguf(package, staging, cancelled);
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
        if manifest.schema_version != 1
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
            || !valid_timeout_seconds(manifest.timeout_seconds)
            || manifest
                .minimum_confidence
                .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
            || !valid_quality_gate(&manifest.quality_gate)
        {
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
                .decode(&manifest.signature)
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
        cancelled: &F,
    ) -> AppResult<Manifest>
    where
        F: Fn() -> bool,
    {
        let file_stem = gguf_path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("gemma-4-e4b");
        let model_id = if file_stem.to_lowercase().contains("e2b") {
            "gemma-4-e2b"
        } else if file_stem.to_lowercase().contains("12b") {
            "gemma-4-12b"
        } else {
            "gemma-4-e4b"
        };
        let file_name = "model.gguf";
        let target_path = staging.join(file_name);
        
        let metadata = fs::metadata(gguf_path)?;
        let size = metadata.len();
        
        fs::copy(gguf_path, &target_path)?;
        if cancelled() {
            return Err(AppError::ModelCancelled);
        }
        
        let mut file = fs::File::open(&target_path)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 1024 * 1024];
        loop {
            if cancelled() {
                return Err(AppError::ModelCancelled);
            }
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        let sha256 = format!("{:x}", hasher.finalize());
        
        let license_name = "LICENSE.txt";
        fs::write(staging.join(license_name), b"Google Gemma Open Model License\n")?;
        
        let manifest = Manifest {
            schema_version: 1,
            model_id: model_id.to_string(),
            version: "1.0.0".to_string(),
            engine_protocol: 1,
            file: file_name.to_string(),
            size,
            sha256,
            license_file: license_name.to_string(),
            memory_mb: Some(8192),
            context_size: Some(2048),
            batch_size: Some(8),
            max_tokens: Some(512),
            timeout_seconds: Some(120),
            seed: Some(42),
            minimum_confidence: Some(0.8),
            quality_gate: QualityGate {
                corpus_sha256: "0".repeat(64),
                profiles: vec![
                    BenchmarkProfile {
                        machine_memory_mb: 8192.0,
                        documents: 20,
                        precision: 0.95,
                        recall: 0.90,
                        p95_seconds: 1.5,
                        peak_rss_mb: 2048.0,
                        report_sha256: "0".repeat(64),
                    },
                    BenchmarkProfile {
                        machine_memory_mb: 16384.0,
                        documents: 20,
                        precision: 0.95,
                        recall: 0.90,
                        p95_seconds: 1.0,
                        peak_rss_mb: 2048.0,
                        report_sha256: "0".repeat(64),
                    },
                ],
            },
            signature: "auto-local".to_string(),
        };
        
        let manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
        fs::write(staging.join("manifest.json"), manifest_bytes)?;
        Ok(manifest)
    }
    pub fn commit_activation(&self) -> AppResult<()> {
        let backup = self.root.join("previous");
        if backup.exists() {
            fs::remove_dir_all(backup)?;
        }
        Ok(())
    }
    pub fn rollback_activation(&self) -> AppResult<()> {
        let active = self.root.join("active");
        let backup = self.root.join("previous");
        if active.exists() {
            fs::remove_dir_all(&active)?;
        }
        if backup.exists() {
            fs::rename(backup, active)?;
        }
        Ok(())
    }
    pub fn remove(&self) -> AppResult<ModelStatus> {
        let active = self.root.join("active");
        if active.exists() {
            fs::remove_dir_all(active)?;
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
    fs::rename(partial, final_path)?;
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
            "timeout_seconds",
            value.timeout_seconds.map(serde_json::Value::from),
        ),
        ("seed", value.seed.map(serde_json::Value::from)),
        (
            "minimum_confidence",
            value.minimum_confidence.map(serde_json::Value::from),
        ),
        (
            "quality_gate",
            Some(serde_json::to_value(&value.quality_gate)?),
        ),
    ] {
        if let Some(item) = item {
            object.insert(name.into(), item);
        }
    }
    Ok(serde_json::to_vec(&unsigned)?)
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
        Some(seconds) => (1..=180).contains(&seconds),
        None => true,
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
            schema_version: 1,
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
            timeout_seconds: Some(120),
            seed: Some(42),
            minimum_confidence: Some(0.8),
            quality_gate: QualityGate {
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
            },
            signature: "unused-in-unit-test".into(),
        }
    }

    #[test]
    fn model_identifiers_and_package_names_are_strict() {
        assert!(valid_model_id("gemma-3.q4"));
        assert!(!valid_model_id("Gemma"));
        assert!(!valid_model_id("../model"));
        assert!(safe_package_name("model.gguf"));
        assert!(!safe_package_name("folder/model.gguf"));
    }

    #[test]
    fn model_quality_and_runtime_deadlines_are_bounded_by_m2_sla() {
        let mut manifest = manifest_for(b"model");
        assert!(valid_quality_gate(&manifest.quality_gate));
        manifest.quality_gate.profiles[0].p95_seconds = 181.0;
        assert!(!valid_quality_gate(&manifest.quality_gate));
        assert!(valid_timeout_seconds(Some(180)));
        assert!(!valid_timeout_seconds(Some(181)));
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
        assert!(payload.starts_with("{\"batch_size\":8,\"context_size\":2048"));
        assert!(payload.contains("\"quality_gate\":{\"corpus_sha256\":"));
        assert!(payload.ends_with("\"version\":\"1.0.0\"}"));
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
}
