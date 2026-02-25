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
