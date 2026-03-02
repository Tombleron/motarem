pub mod config;

use anyhow::Result;
use config::TcpServerConfig;
use futures::{SinkExt, StreamExt};
use motarem_core::controller_manager::ControllerManager;
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};
use tokio::{
    net::{TcpListener, TcpStream},
    sync::broadcast,
};
use tokio_util::codec::{Framed, LinesCodec};
use tracing::{debug, error, info, warn};

use crate::server_core::ServerService;

pub struct TcpServer {
    config: TcpServerConfig,
    service: Arc<ServerService>,
    shutdown_tx: Option<broadcast::Sender<()>>,
}

impl TcpServer {
    pub fn new(config: TcpServerConfig, manager: Arc<ControllerManager>) -> Self {
        Self {
            config,
            service: Arc::new(ServerService::new(manager)),
            shutdown_tx: None,
        }
    }

    pub fn with_service(config: TcpServerConfig, service: Arc<ServerService>) -> Self {
        Self {
            config,
            service,
            shutdown_tx: None,
        }
    }

    pub async fn start(&mut self) -> Result<()> {
        let bind_addr = self.config.bind_addr.clone();
        let listener = TcpListener::bind(&bind_addr).await?;
        info!(bind_addr = %bind_addr, "TCP server listening");

        let (shutdown_tx, mut shutdown_rx) = broadcast::channel(1);
        self.shutdown_tx = Some(shutdown_tx);

        let service = Arc::clone(&self.service);
        let max_connections = self.config.max_connections;
        let max_line_len = self.config.buffer_size;

        tokio::spawn(async move {
            let active_connections = Arc::new(AtomicUsize::new(0));

            loop {
                tokio::select! {
                    accept_result = listener.accept() => {
                        match accept_result {
                            Ok((stream, peer_addr)) => {
                                let current = active_connections.load(Ordering::Relaxed);
                                if current >= max_connections {
                                    warn!(
                                        peer_addr = %peer_addr,
                                        current_connections = current,
                                        max_connections = max_connections,
                                        "Maximum connections reached, rejecting new connection"
                                    );
                                    continue;
                                }

                                active_connections.fetch_add(1, Ordering::Relaxed);
                                let new_count = active_connections.load(Ordering::Relaxed);
                                debug!(
                                    peer_addr = %peer_addr,
                                    active_connections = new_count,
                                    "New TCP client connected"
                                );

                                let service_clone = Arc::clone(&service);
                                let mut client_shutdown_rx = shutdown_rx.resubscribe();
                                let active_connections_clone = Arc::clone(&active_connections);

                                tokio::spawn(async move {
                                    let result = Self::handle_client(
                                        stream,
                                        service_clone,
                                        &mut client_shutdown_rx,
                                        max_line_len,
                                    )
                                    .await;

                                    if let Err(e) = result {
                                        error!(error = %e, peer_addr = %peer_addr, "TCP client handler error");
                                    }

                                    let remaining = active_connections_clone.fetch_sub(1, Ordering::Relaxed) - 1;
                                    debug!(
                                        peer_addr = %peer_addr,
                                        active_connections = remaining,
                                        "TCP client disconnected"
                                    );
                                });
                            }
                            Err(e) => {
                                error!(error = %e, "Failed to accept TCP connection");
                            }
                        }
                    }
                    _ = shutdown_rx.recv() => {
                        info!("TCP server shutting down");
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

        info!("TCP server shutdown signal sent");
        Ok(())
    }

    async fn handle_client(
        stream: TcpStream,
        service: Arc<ServerService>,
        shutdown_rx: &mut broadcast::Receiver<()>,
        max_line_len: usize,
    ) -> Result<()> {
        let mut framed = Framed::new(stream, LinesCodec::new_with_max_length(max_line_len));

        loop {
            tokio::select! {
                line_result = framed.next() => {
                    match line_result {
                        Some(Ok(line)) => {
                            debug!(command = %line, "Received TCP command");

                            let response_line = service.handle_line(&line).await?;
                            if let Err(e) = framed.send(response_line).await {
                                error!(error = %e, "Failed to send TCP response");
                                break;
                            }
                        }
                        Some(Err(e)) => {
                            error!(error = %e, "Error reading from TCP client");
                            break;
                        }
                        None => {
                            debug!("TCP client disconnected");
                            break;
                        }
                    }
                }
                _ = shutdown_rx.recv() => {
                    debug!("Shutdown signal received, closing TCP client connection");
                    break;
                }
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {}
