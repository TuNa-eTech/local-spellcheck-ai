use crate::error::{AppError, AppResult};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelStatus {
    pub state: String,
    pub model_id: Option<String>,
    pub version: Option<String>,
    pub code: Option<String>,
}

#[derive(Debug, Deserialize)]
struct Manifest {
    schema_version: u8,
    model_id: String,
    version: String,
    engine_protocol: u8,
    file: String,
    size: u64,
    sha256: String,
    license_file: String,
    signature: String,
}

pub struct ModelProvisioner {
    root: PathBuf,
}

impl ModelProvisioner {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }
    pub fn status(&self) -> ModelStatus {
        let manifest = self.root.join("active/manifest.json");
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
                state: "not_installed".into(),
                model_id: None,
                version: None,
                code: None,
            },
        }
    }
    pub fn import(&self, package: &Path) -> AppResult<ModelStatus> {
        let staging = self.root.join(format!("staging-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&staging)?;
        let result = self
            .verify_and_extract(package, &staging)
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
    fn verify_and_extract(&self, package: &Path, staging: &Path) -> AppResult<Manifest> {
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
        if manifest.schema_version != 1
            || manifest.engine_protocol != 1
            || !safe_package_name(&manifest.file)
            || !safe_package_name(&manifest.license_file)
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
        let model_bytes = read_entry(
            &mut archive,
            &manifest.file,
            manifest.size.saturating_add(1),
        )?;
        if model_bytes.len() as u64 != manifest.size
            || format!("{:x}", Sha256::digest(&model_bytes)) != manifest.sha256
        {
            return Err(AppError::ModelPackageInvalid);
        }
        let license = read_entry(&mut archive, &manifest.license_file, 1024 * 1024)?;
        fs::write(staging.join(&manifest.file), model_bytes)?;
        fs::write(staging.join(&manifest.license_file), license)?;
        fs::write(staging.join("manifest.json"), manifest_bytes)?;
        Ok(manifest)
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
fn canonical_unsigned(value: &Manifest) -> AppResult<Vec<u8>> {
    Ok(serde_json::to_vec(
        &serde_json::json!({"schema_version":value.schema_version,"model_id":value.model_id,"version":value.version,"engine_protocol":value.engine_protocol,"file":value.file,"size":value.size,"sha256":value.sha256,"license_file":value.license_file}),
    )?)
}

fn safe_package_name(value: &str) -> bool {
    !value.is_empty() && value != "." && !value.contains('/') && !value.contains('\\')
}
