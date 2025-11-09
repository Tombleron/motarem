#[derive(Debug)]
pub enum ProtocolError {
    InvalidJson(String),
    UnsupportedCommand(String),
    MissingField(String),
    IoError(std::io::Error),
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProtocolError::InvalidJson(msg) => write!(f, "Invalid JSON: {}", msg),
            ProtocolError::UnsupportedCommand(cmd) => write!(f, "Unsupported command: {}", cmd),
            ProtocolError::MissingField(field) => write!(f, "Missing required field: {}", field),
            ProtocolError::IoError(err) => write!(f, "IO error: {}", err),
        }
    }
}

impl std::error::Error for ProtocolError {}

impl From<serde_json::Error> for ProtocolError {
    fn from(err: serde_json::Error) -> Self {
        ProtocolError::InvalidJson(err.to_string())
    }
}

impl From<std::io::Error> for ProtocolError {
    fn from(err: std::io::Error) -> Self {
        ProtocolError::IoError(err)
    }
}
