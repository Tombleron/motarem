use crate::axis::movement_parameters::MovementParams;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ClientCommand {
    #[serde(rename = "move")]
    Move {
        controller: String,
        axis: String,
        target: f64,
        #[serde(default)]
        params: Option<MovementParams>,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "stop")]
    Stop {
        controller: String,
        axis: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "get_state")]
    GetState {
        controller: String,
        axis: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "get_position")]
    GetPosition {
        controller: String,
        axis: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "get_attribute")]
    GetAttribute {
        controller: String,
        axis: String,
        attribute: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "get_available_params")]
    GetAvailableParams {
        controller: String,
        axis: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "get_supported_movement_params")]
    GetSupportedMovementParams {
        controller: String,
        axis: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "list_controllers")]
    ListControllers {
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "list_axes")]
    ListAxes {
        controller: String,
        #[serde(default)]
        id: Option<String>,
    },
    #[serde(rename = "ping")]
    Ping {
        #[serde(default)]
        id: Option<String>,
    },
}

impl ClientCommand {
    pub fn id(&self) -> Option<&String> {
        match self {
            ClientCommand::Move { id, .. } => id.as_ref(),
            ClientCommand::Stop { id, .. } => id.as_ref(),
            ClientCommand::GetState { id, .. } => id.as_ref(),
            ClientCommand::GetPosition { id, .. } => id.as_ref(),
            ClientCommand::GetAttribute { id, .. } => id.as_ref(),
            ClientCommand::GetAvailableParams { id, .. } => id.as_ref(),
            ClientCommand::GetSupportedMovementParams { id, .. } => id.as_ref(),
            ClientCommand::ListControllers { id, .. } => id.as_ref(),
            ClientCommand::ListAxes { id, .. } => id.as_ref(),
            ClientCommand::Ping { id, .. } => id.as_ref(),
        }
    }
}
