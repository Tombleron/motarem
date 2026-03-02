use anyhow::Result;
use motarem_core::controller_manager::command::MotaremResponse;
use motarem_core::controller_manager::{command::Command, ControllerManager};
use motarem_proto::{
    client_command::ClientCommand, parse_command, serialize_response,
    server_response::ServerResponse,
};
use std::{sync::Arc, time::Duration};
use tokio::sync::oneshot;
use tokio::time::timeout;

/// Transport-agnostic service responsible for:
/// - parsing incoming command lines
/// - executing commands via ControllerManager
/// - mapping errors to protocol responses
/// - serializing responses to outbound lines
pub struct ServerService {
    manager: Arc<ControllerManager>,
    command_timeout: Duration,
}

impl ServerService {
    /// Creates a service with a sensible default command timeout.
    pub fn new(manager: Arc<ControllerManager>) -> Self {
        Self {
            manager,
            command_timeout: Duration::from_secs(10),
        }
    }

    /// Creates a service with a custom command timeout.
    pub fn with_timeout(manager: Arc<ControllerManager>, command_timeout: Duration) -> Self {
        Self {
            manager,
            command_timeout,
        }
    }

    /// Handles one inbound text line and returns a serialized response line.
    /// This method never propagates protocol/business errors; those are converted
    /// into `ServerResponse::error`. It only fails if response serialization fails.
    pub async fn handle_line(&self, line: &str) -> Result<String> {
        let response = self.process_command(line).await;
        serialize_response(&response).map_err(anyhow::Error::from)
    }

    async fn process_command(&self, line: &str) -> ServerResponse {
        let command = match parse_command(line) {
            Ok(cmd) => cmd,
            Err(e) => {
                return ServerResponse::error(None, format!("Failed to parse command: {e}"));
            }
        };

        let command_id = command.id().cloned();

        let result = timeout(self.command_timeout, self.execute_command(command)).await;

        match result {
            Ok(Ok(data)) => ServerResponse::success(command_id, data),
            Ok(Err(e)) => ServerResponse::error(command_id, e.to_string()),
            Err(_) => ServerResponse::error(
                command_id,
                format!(
                    "Command execution timed out after {:?}",
                    self.command_timeout
                ),
            ),
        }
    }

    async fn execute_command(&self, command: ClientCommand) -> Result<MotaremResponse> {
        match command {
            ClientCommand::Move {
                controller,
                axis,
                target,
                params,
                ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::Move {
                    controller,
                    axis,
                    target,
                    params,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::Stop {
                controller, axis, ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::Stop {
                    controller,
                    axis,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::GetState {
                controller, axis, ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::GetState {
                    controller,
                    axis,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::GetPosition {
                controller, axis, ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::GetPos {
                    controller,
                    axis,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::GetAttribute {
                controller,
                axis,
                attribute,
                ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::GetAttr {
                    controller,
                    axis,
                    attr: attribute,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::GetAvailableParams {
                controller, axis, ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::GetAvailableParams {
                    controller,
                    axis,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::GetSupportedMovementParams {
                controller, axis, ..
            } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::GetSupportedMovementParams {
                    controller,
                    axis,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::ListControllers { .. } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::ListControllers { resp: tx };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::ListAxes { controller, .. } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::ListAxes {
                    controller,
                    resp: tx,
                };
                self.manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::Ping { .. } => Ok(MotaremResponse::Pong),
        }
    }
}
