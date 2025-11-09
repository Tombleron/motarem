pub mod config;

use anyhow::Result;
use futures::{SinkExt, StreamExt};
use serde_json::json;
use std::{
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};
use tokio::{
    net::{UnixListener, UnixStream},
    sync::oneshot,
};
use tokio_util::codec::{Framed, LinesCodec};
use tracing::{debug, error, info, warn};

use crate::{
    controller_manager::{command::Command, ControllerManager},
    protocol::{
        client_command::ClientCommand, parse_command, serialize_response,
        server_response::ServerResponse,
    },
};
use config::SocketServerConfig;

pub struct SocketServer {
    config: SocketServerConfig,
    manager: Arc<ControllerManager>,
    shutdown_tx: Option<tokio::sync::broadcast::Sender<()>>,
}

impl SocketServer {
    pub fn new(config: SocketServerConfig, manager: Arc<ControllerManager>) -> Self {
        Self {
            config,
            manager,
            shutdown_tx: None,
        }
    }

    pub async fn start(&mut self) -> Result<()> {
        if Path::new(&self.config.socket_path).exists() {
            tokio::fs::remove_file(&self.config.socket_path).await?;
        }

        let listener = UnixListener::bind(&self.config.socket_path)?;
        info!("Socket server listening on: {}", self.config.socket_path);

        let (shutdown_tx, mut shutdown_rx) = tokio::sync::broadcast::channel(1);
        self.shutdown_tx = Some(shutdown_tx);

        let manager = self.manager.clone();
        let max_connections = self.config.max_connections;

        tokio::spawn(async move {
            let active_connections = Arc::new(AtomicUsize::new(0));

            loop {
                tokio::select! {
                    accept_result = listener.accept() => {
                        match accept_result {
                            Ok((stream, _addr)) => {
                                let current_connections = active_connections.load(Ordering::Relaxed);
                                if current_connections >= max_connections {
                                    warn!("Maximum connections reached ({}), rejecting new connection", current_connections);
                                    continue;
                                }

                                active_connections.fetch_add(1, Ordering::Relaxed);
                                let new_count = active_connections.load(Ordering::Relaxed);
                                debug!("New client connected. Active connections: {}", new_count);

                                let manager_clone = manager.clone();
                                let mut shutdown_rx_clone = shutdown_rx.resubscribe();
                                let active_connections_clone = active_connections.clone();

                                tokio::spawn(async move {
                                    let result = Self::handle_client(stream, manager_clone, &mut shutdown_rx_clone).await;
                                    if let Err(e) = result {
                                        error!("Client handler error: {}", e);
                                    }

                                    let remaining = active_connections_clone.fetch_sub(1, Ordering::Relaxed) - 1;
                                    debug!("Client disconnected. Active connections: {}", remaining);
                                });
                            }
                            Err(e) => {
                                error!("Failed to accept connection: {}", e);
                            }
                        }
                    }
                    _ = shutdown_rx.recv() => {
                        info!("Socket server shutting down");
                        break;
                    }
                }
            }
        });

        Ok(())
    }

    pub async fn shutdown(&self) -> Result<()> {
        if let Some(shutdown_tx) = &self.shutdown_tx {
            let _ = shutdown_tx.send(());
        }

        // Remove socket file
        if Path::new(&self.config.socket_path).exists() {
            tokio::fs::remove_file(&self.config.socket_path).await?;
        }

        info!("Socket server shutdown complete");
        Ok(())
    }

    async fn handle_client(
        stream: UnixStream,
        manager: Arc<ControllerManager>,
        shutdown_rx: &mut tokio::sync::broadcast::Receiver<()>,
    ) -> Result<()> {
        let mut framed = Framed::new(stream, LinesCodec::new());

        loop {
            tokio::select! {
                line_result = framed.next() => {
                    match line_result {
                        Some(Ok(line)) => {
                            debug!("Received command: {}", line);

                            let response = Self::process_command(&line, &manager).await;
                            let response_json = serialize_response(&response)?;

                            if let Err(e) = framed.send(response_json).await {
                                error!("Failed to send response: {}", e);
                                break;
                            }
                        }
                        Some(Err(e)) => {
                            error!("Error reading from client: {}", e);
                            break;
                        }
                        None => {
                            debug!("Client disconnected");
                            break;
                        }
                    }
                }
                _ = shutdown_rx.recv() => {
                    debug!("Shutdown signal received, closing client connection");
                    break;
                }
            }
        }

        Ok(())
    }

    async fn process_command(line: &str, manager: &ControllerManager) -> ServerResponse {
        let command = match parse_command(line) {
            Ok(cmd) => cmd,
            Err(e) => {
                return ServerResponse::error(None, format!("Failed to parse command: {}", e));
            }
        };

        let command_id = command.id().cloned();

        let result = Self::execute_command(command, manager).await;

        match result {
            Ok(data) => ServerResponse::success(command_id, data),
            Err(e) => ServerResponse::error(command_id, e.to_string()),
        }
    }

    async fn execute_command(
        command: ClientCommand,
        manager: &ControllerManager,
    ) -> Result<serde_json::Value> {
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
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
                manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::ListControllers { .. } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::ListControllers { resp: tx };
                manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::ListAxes { controller, .. } => {
                let (tx, rx) = oneshot::channel();
                let cmd = Command::ListAxes {
                    controller,
                    resp: tx,
                };
                manager.send_command(cmd).await?;
                rx.await?
            }
            ClientCommand::Ping { .. } => Ok(json!({
                "message": "pong",
                "timestamp": chrono::Utc::now().to_rfc3339()
            })),
        }
    }
}

#[cfg(test)]
mod tests {}
