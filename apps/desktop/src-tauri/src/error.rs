use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("ENGINE_NOT_AVAILABLE")]
    EngineUnavailable,
    #[error("ENGINE_PROTOCOL_ERROR")]
    EngineProtocol,
    #[error("ENGINE_TIMEOUT")]
    EngineTimeout,
    #[error("DOCUMENT_INVALID_PATH")]
    InvalidPath,
    #[error("DOCUMENT_INVALID_TYPE")]
    InvalidType,
    #[error("OUTPUT_WRITE_FAILED")]
    OutputWrite,
    #[error("MODEL_NOT_CONFIGURED")]
    ModelNotConfigured,
    #[error("MODEL_PACKAGE_INVALID")]
    ModelPackageInvalid,
    #[error("MODEL_SIGNATURE_INVALID")]
    ModelSignatureInvalid,
    #[error("{0}")]
    Engine(String),
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error("{0}")]
    Json(#[from] serde_json::Error),
}

pub type AppResult<T> = Result<T, AppError>;

impl serde::Serialize for AppError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(&self.to_string())
    }
}
