use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status")]
pub enum ServerResponse {
    #[serde(rename = "success")]
    Success {
        #[serde(skip_serializing_if = "Option::is_none")]
        id: Option<String>,
        data: serde_json::Value,
    },
    #[serde(rename = "error")]
    Error {
        #[serde(skip_serializing_if = "Option::is_none")]
        id: Option<String>,
        message: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        code: Option<String>,
    },
}

impl ServerResponse {
    pub fn success(id: Option<String>, data: serde_json::Value) -> Self {
        Self::Success { id, data }
    }

    pub fn error(id: Option<String>, message: String) -> Self {
        Self::Error {
            id,
            message,
            code: None,
        }
    }

    pub fn error_with_code(id: Option<String>, message: String, code: String) -> Self {
        Self::Error {
            id,
            message,
            code: Some(code),
        }
    }
}
