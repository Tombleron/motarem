pub mod client_command;
pub mod error;
pub mod server_response;

use client_command::ClientCommand;
use error::ProtocolError;
use server_response::ServerResponse;

use anyhow::Result;

pub fn parse_command(json_str: &str) -> Result<ClientCommand, ProtocolError> {
    serde_json::from_str(json_str).map_err(ProtocolError::from)
}

pub fn serialize_response(response: &ServerResponse) -> Result<String, ProtocolError> {
    serde_json::to_string(response).map_err(ProtocolError::from)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_move_command() {
        let json = r#"{"type": "move", "controller": "test", "axis": "X", "target": 100.0}"#;
        let cmd = parse_command(json).unwrap();

        match cmd {
            ClientCommand::Move {
                controller,
                axis,
                target,
                ..
            } => {
                assert_eq!(controller, "test");
                assert_eq!(axis, "X");
                assert_eq!(target, 100.0);
            }
            _ => panic!("Expected Move command"),
        }
    }

    #[test]
    fn test_serialize_success_response() {
        let response = ServerResponse::success(
            Some("test-id".to_string()),
            serde_json::json!({"result": "ok"}),
        );

        let json = serialize_response(&response).unwrap();
        assert!(json.contains("success"));
        assert!(json.contains("test-id"));
    }

    #[test]
    fn test_serialize_error_response() {
        let response = ServerResponse::error(
            Some("test-id".to_string()),
            "Something went wrong".to_string(),
        );

        let json = serialize_response(&response).unwrap();
        assert!(json.contains("error"));
        assert!(json.contains("Something went wrong"));
    }
}
