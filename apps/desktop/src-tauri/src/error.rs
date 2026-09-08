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
    #[error("MODEL_DISK_SPACE_INSUFFICIENT")]
    ModelDiskSpace,
    #[error("MODEL_OPERATION_CANCELLED")]
    ModelCancelled,
    #[error("MODEL_OPERATION_IN_PROGRESS")]
    ModelOperationInProgress,
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
        log::warn!(target: "host", "command failed: {self:?}");
        serializer.serialize_str(&self.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_error_code_is_preserved_for_the_frontend() {
        let error = AppError::Engine("CUSTOM_PROMPT_CONTEXT_EXCEEDED".into());
        assert_eq!(
            serde_json::to_string(&error).unwrap(),
            "\"CUSTOM_PROMPT_CONTEXT_EXCEEDED\""
        );
    }
}
