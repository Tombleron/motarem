use anyhow::Result;
use motarem::{
    axis::{
        movement_parameters::MovementParams, state::AxisState, state_info::AxisStateInfo, Axis,
    },
    controller_manager::{command::Command, config::ManagerConfig, ControllerManager},
    motor_controller::MotorController,
    socket_server::{config::SocketServerConfig, SocketServer},
};
use std::{sync::Arc, time::Duration};
use tokio::sync::oneshot;
use tracing::{error, info};

struct MockController {
    name: String,
    axes: Vec<Arc<dyn Axis>>,
}

impl MockController {
    fn new(name: String) -> Self {
        let axes: Vec<Arc<dyn Axis>> = vec![
            Arc::new(MockAxis::new("X".to_string(), true)),
            Arc::new(MockAxis::new("Y".to_string(), true)),
            Arc::new(MockAxis::new("Z".to_string(), false)),
        ];
        Self { name, axes }
    }
}

#[async_trait::async_trait]
impl MotorController for MockController {
    fn name(&self) -> &str {
        &self.name
    }

    fn axes(&self) -> Vec<Arc<dyn Axis>> {
        self.axes.clone()
    }

    async fn shutdown(&self) -> Result<()> {
        info!("Shutting down controller: {}", self.name);
        Ok(())
    }

    async fn start(&self, axis: &str, target: f64, params: Option<MovementParams>) -> Result<()> {
        let ax = self.get_axis(axis)?;
        ax.start(target, params).await
    }

    async fn stop(&self, axis: &str) -> Result<()> {
        let ax = self.get_axis(axis)?;
        ax.stop().await
    }

    async fn state(&self, axis: &str) -> Result<AxisStateInfo> {
        let ax = self.get_axis(axis)?;
        ax.get_state().await
    }

    async fn get_attribute(&self, axis: &str, attribute: &str) -> Result<f64> {
        let ax = self.get_axis(axis)?;
        ax.get_attribute(attribute).await
    }
}

// Example implementation of a mock axis with configurable capabilities
struct MockAxis {
    name: String,
    position: tokio::sync::RwLock<f64>,
    state: tokio::sync::RwLock<AxisState>,
    supports_acceleration: bool,
    velocity: f64,
    acceleration: f64,
    max_position: f64,
    min_position: f64,
}

impl MockAxis {
    fn new(name: String, supports_acceleration: bool) -> Self {
        Self {
            name,
            position: tokio::sync::RwLock::new(0.0),
            state: tokio::sync::RwLock::new(AxisState::On),
            supports_acceleration,
            velocity: 100.0,
            acceleration: 1000.0,
            max_position: 1000.0,
            min_position: -1000.0,
        }
    }
}

#[async_trait::async_trait]
impl Axis for MockAxis {
    fn name(&self) -> &str {
        &self.name
    }

    async fn start(&self, target: f64, params: Option<MovementParams>) -> Result<()> {
        if let Some(ref params) = params {
            info!(
                "Moving axis {} to position {} with parameters: velocity={:?}, acceleration={:?}, deceleration={:?}, custom={:?}",
                self.name, target, params.velocity, params.acceleration, params.deceleration, params.custom
            );
        } else {
            info!(
                "Moving axis {} to position {} with default parameters",
                self.name, target
            );
        }

        if let Some(ref params) = params {
            if params.acceleration.is_some() && !self.supports_acceleration {
                return Err(anyhow::anyhow!(
                    "Axis {} does not support acceleration parameter",
                    self.name
                ));
            }
        }

        *self.position.write().await = target;
        *self.state.write().await = AxisState::Moving;

        let movement_time = if let Some(ref params) = params {
            let velocity = params.velocity.unwrap_or(self.velocity);
            let current_pos = *self.position.read().await;
            let distance = (target - current_pos).abs();
            (distance / velocity * 1000.0) as u64
        } else {
            1000
        };

        tokio::time::sleep(Duration::from_millis(movement_time)).await;
        *self.state.write().await = AxisState::On;
        Ok(())
    }

    async fn stop(&self) -> Result<()> {
        info!("Stopping axis {}", self.name);
        *self.state.write().await = AxisState::On;
        Ok(())
    }

    async fn get_position(&self) -> Result<f64> {
        Ok(*self.position.read().await)
    }

    async fn get_state(&self) -> Result<AxisStateInfo> {
        let state = *self.state.read().await;
        Ok(AxisStateInfo::new(state))
    }

    async fn get_attribute(&self, name: &str) -> Result<f64> {
        match name {
            "velocity" => Ok(self.velocity),
            "acceleration" => {
                if self.supports_acceleration {
                    Ok(self.acceleration)
                } else {
                    Err(anyhow::anyhow!(
                        "Parameter '{}' not supported by axis {}",
                        name,
                        self.name
                    ))
                }
            }
            "max_position" => Ok(self.max_position),
            "min_position" => Ok(self.min_position),
            "position" => self.get_position().await,
            "supports_acceleration" => Ok(if self.supports_acceleration { 1.0 } else { 0.0 }),
            _ => Err(anyhow::anyhow!("Unknown parameter: {}", name)),
        }
    }

    async fn get_available_params(&self) -> Result<Vec<String>> {
        let mut params = vec![
            "velocity".to_string(),
            "max_position".to_string(),
            "min_position".to_string(),
            "position".to_string(),
            "supports_acceleration".to_string(),
        ];

        if self.supports_acceleration {
            params.push("acceleration".to_string());
        }

        Ok(params)
    }

    async fn get_supported_movement_params(&self) -> Result<Vec<String>> {
        let mut params = vec!["velocity".to_string()];

        if self.supports_acceleration {
            params.push("acceleration".to_string());
            params.push("deceleration".to_string());
        }

        Ok(params)
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    info!("Starting Motarem - Motor Controller Manager with Socket Server");

    let config = ManagerConfig {
        default_ttl: Duration::from_secs(5),
        cache_capacity: 1000,
    };

    let manager = Arc::new(ControllerManager::new(config));

    let mock_controller = Arc::new(MockController::new("mock_ctrl_1".to_string()));
    manager
        .register_controller("mock_ctrl_1".to_string(), mock_controller)
        .await?;

    info!("Registered mock controller with axes: X (with acceleration), Y (with acceleration), Z (basic)");

    let socket_config = SocketServerConfig {
        socket_path: "/tmp/motarem.sock".to_string(),
        max_connections: 50,
        buffer_size: 8192,
    };

    let mut socket_server = SocketServer::new(socket_config, manager.clone());
    socket_server.start().await?;

    info!("Socket server started at /tmp/motarem.sock");
    info!("You can connect using the client examples or tools like socat:");
    info!("  socat - UNIX-CONNECT:/tmp/motarem.sock");

    let movement_params = MovementParams::new()
        .with_velocity(150.0)
        .with_acceleration(2000.0);

    let (tx, rx) = oneshot::channel();
    let move_cmd = Command::Move {
        controller: "mock_ctrl_1".to_string(),
        axis: "X".to_string(),
        target: 100.0,
        params: Some(movement_params),
        resp: tx,
    };

    manager.send_command(move_cmd).await?;
    if let Ok(result) = rx.await {
        match result {
            Ok(value) => info!("X-axis move command result: {}", value),
            Err(e) => error!("X-axis move command failed: {}", e),
        }
    }

    let movement_params = MovementParams::new()
        .with_velocity(120.0)
        .with_acceleration(1500.0);

    let (tx, rx) = oneshot::channel();
    let move_cmd = Command::Move {
        controller: "mock_ctrl_1".to_string(),
        axis: "Y".to_string(),
        target: 50.0,
        params: Some(movement_params),
        resp: tx,
    };

    manager.send_command(move_cmd).await?;
    if let Ok(result) = rx.await {
        match result {
            Ok(value) => info!("Y-axis move command result: {}", value),
            Err(e) => error!("Y-axis move command failed: {}", e),
        }
    }

    let movement_params = MovementParams::new()
        .with_velocity(80.0)
        .with_acceleration(500.0);

    let (tx, rx) = oneshot::channel();
    let move_cmd = Command::Move {
        controller: "mock_ctrl_1".to_string(),
        axis: "Z".to_string(),
        target: 25.0,
        params: Some(movement_params),
        resp: tx,
    };

    manager.send_command(move_cmd).await?;
    if let Ok(result) = rx.await {
        match result {
            Ok(value) => info!("Z-axis move command result: {}", value),
            Err(e) => error!("Z-axis move command failed (expected): {}", e),
        }
    }

    let movement_params = MovementParams::new().with_velocity(80.0);

    let (tx, rx) = oneshot::channel();
    let move_cmd = Command::Move {
        controller: "mock_ctrl_1".to_string(),
        axis: "Z".to_string(),
        target: 25.0,
        params: Some(movement_params),
        resp: tx,
    };

    manager.send_command(move_cmd).await?;
    if let Ok(result) = rx.await {
        match result {
            Ok(value) => info!("Z-axis move command result: {}", value),
            Err(e) => error!("Z-axis move command failed: {}", e),
        }
    }

    for axis in ["X", "Y", "Z"] {
        let (tx, rx) = oneshot::channel();
        let params_cmd = Command::GetSupportedMovementParams {
            controller: "mock_ctrl_1".to_string(),
            axis: axis.to_string(),
            resp: tx,
        };

        manager.send_command(params_cmd).await?;
        if let Ok(result) = rx.await {
            match result {
                Ok(value) => info!("Supported movement parameters for {}: {}", axis, value),
                Err(e) => error!(
                    "Failed to get supported movement parameters for {}: {}",
                    axis, e
                ),
            }
        }
    }

    for axis in ["X", "Y", "Z"] {
        let (tx, rx) = oneshot::channel();
        let params_cmd = Command::GetAvailableParams {
            controller: "mock_ctrl_1".to_string(),
            axis: axis.to_string(),
            resp: tx,
        };

        manager.send_command(params_cmd).await?;
        if let Ok(result) = rx.await {
            match result {
                Ok(value) => info!("Available parameters for {}: {}", axis, value),
                Err(e) => error!("Failed to get available parameters for {}: {}", axis, e),
            }
        }
    }

    let (tx, rx) = oneshot::channel();
    let attr_cmd = Command::GetAttr {
        controller: "mock_ctrl_1".to_string(),
        axis: "X".to_string(),
        attr: "velocity".to_string(),
        resp: tx,
    };

    manager.send_command(attr_cmd).await?;
    if let Ok(result) = rx.await {
        match result {
            Ok(value) => info!("Get attribute result: {}", value),
            Err(e) => error!("Get attribute failed: {}", e),
        }
    }

    tokio::time::sleep(Duration::from_secs(2)).await;

    info!("Server is now running. You can test it using socat:");
    info!(r#"  echo '{{"type": "ping"}}' | socat - UNIX-CONNECT:/tmp/motarem.sock"#);

    info!("Server will run for 600 seconds for manual testing...");
    tokio::time::sleep(Duration::from_secs(600)).await;

    info!("Shutting down...");
    socket_server.shutdown().await?;
    manager.unregister_controller("mock_ctrl_1").await?;
    info!("Motarem shutdown complete");

    Ok(())
}
