pub mod command;
pub mod config;

use command::Command;
use config::ManagerConfig;

use anyhow::Result;
use moka::future::Cache;
use serde_json::{json, Value};
use std::{collections::HashMap, sync::Arc};
use tokio::sync::{mpsc, RwLock};

use crate::{axis::movement_parameters::MovementParams, motor_controller::MotorController};

pub struct ControllerManager {
    controllers: Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
    cmd_sender: mpsc::Sender<Command>,
    cache: Cache<String, Value>,
    config: ManagerConfig,
}

impl ControllerManager {
    pub fn new(config: ManagerConfig) -> Self {
        let cache = Cache::builder()
            .max_capacity(config.cache_capacity as u64)
            .build();

        let (tx, rx) = mpsc::channel::<Command>(100);

        let controllers = Arc::new(RwLock::new(HashMap::new()));
        let cache_clone = cache.clone();
        let controllers_clone = controllers.clone();

        tokio::spawn(Self::command_loop(controllers_clone, cache_clone, rx));

        ControllerManager {
            controllers,
            cmd_sender: tx,
            cache,
            config,
        }
    }

    pub async fn register_controller(
        &self,
        name: String,
        controller: Arc<dyn MotorController>,
    ) -> Result<()> {
        // controller.initialize().await?;
        let mut ctrls = self.controllers.write().await;
        ctrls.insert(name, controller);
        Ok(())
    }

    pub async fn unregister_controller(&self, name: &str) -> Result<()> {
        let mut ctrls = self.controllers.write().await;
        if let Some(ctrl) = ctrls.remove(name) {
            ctrl.shutdown().await?;
        }
        Ok(())
    }

    pub async fn send_command(&self, cmd: Command) -> Result<()> {
        self.cmd_sender.send(cmd).await?;
        Ok(())
    }

    pub fn cache(&self) -> &Cache<String, Value> {
        &self.cache
    }

    pub fn config(&self) -> &ManagerConfig {
        &self.config
    }

    async fn command_loop(
        controllers: Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: Cache<String, Value>,
        mut rx: mpsc::Receiver<Command>,
    ) {
        while let Some(cmd) = rx.recv().await {
            match cmd {
                Command::Move {
                    controller,
                    axis,
                    target,
                    params,
                    resp,
                } => {
                    let result =
                        Self::handle_move(&controllers, &cache, &controller, &axis, target, params)
                            .await;
                    let _ = resp.send(result);
                }
                Command::Stop {
                    controller,
                    axis,
                    resp,
                } => {
                    let result = Self::handle_stop(&controllers, &controller, &axis).await;
                    let _ = resp.send(result);
                }
                Command::GetState {
                    controller,
                    axis,
                    resp,
                } => {
                    let result =
                        Self::handle_get_state(&controllers, &cache, &controller, &axis).await;
                    let _ = resp.send(result);
                }
                Command::GetPos {
                    controller,
                    axis,
                    resp,
                } => {
                    let result =
                        Self::handle_get_pos(&controllers, &cache, &controller, &axis).await;
                    let _ = resp.send(result);
                }
                Command::GetAttr {
                    controller,
                    axis,
                    attr,
                    resp,
                } => {
                    let result =
                        Self::handle_get_attr(&controllers, &cache, &controller, &axis, &attr)
                            .await;
                    let _ = resp.send(result);
                }
                Command::GetAvailableParams {
                    controller,
                    axis,
                    resp,
                } => {
                    let result =
                        Self::handle_get_available_params(&controllers, &controller, &axis).await;
                    let _ = resp.send(result);
                }
                Command::GetSupportedMovementParams {
                    controller,
                    axis,
                    resp,
                } => {
                    let result = Self::handle_get_supported_movement_params(
                        &controllers,
                        &controller,
                        &axis,
                    )
                    .await;
                    let _ = resp.send(result);
                }
                Command::ListControllers { resp } => {
                    let result = Self::handle_list_controllers(&controllers).await;
                    let _ = resp.send(result);
                }
                Command::ListAxes { controller, resp } => {
                    let result = Self::handle_list_axes(&controllers, &controller).await;
                    let _ = resp.send(result);
                }
            }
        }
    }

    async fn handle_move(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, Value>,
        controller: &str,
        axis: &str,
        target: f64,
        params: Option<MovementParams>,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        ctrl.start(axis, target, params).await?;

        let cache_key = format!("{}::{}::position", controller, axis);
        cache.invalidate(&cache_key).await;
        Ok(json!({"status": "ok", "action": "move", "target": target}))
    }

    async fn handle_stop(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        ctrl.stop(axis).await?;
        Ok(json!({"status": "ok", "action": "stop"}))
    }

    async fn handle_get_pos(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, Value>,
        controller: &str,
        axis: &str,
    ) -> Result<Value> {
        let cache_key = format!("{}::{}::position", controller, axis);

        dbg!("Checking cache");
        if let Some(val) = cache.get(&cache_key).await {
            dbg!("Cache hit");
            return Ok(json!({"controller": controller, "axis": axis, "position": val}));
        }

        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        let ax = ctrl.get_axis(axis)?;

        let pos = ax.get_position().await?;
        let value = json!(pos);

        dbg!("inserting chache", &cache_key, &value);
        let _ = cache.insert(cache_key.clone(), value.clone()).await;

        Ok(json!({"controller": controller, "axis": axis, "position": value}))
    }

    async fn handle_get_state(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, Value>,
        controller: &str,
        axis: &str,
    ) -> Result<Value> {
        let cache_key = format!("{}::{}::status", controller, axis);
        if let Some(val) = cache.get(&cache_key).await {
            return Ok(json!({"controller": controller, "axis": axis, "status": val}));
        }
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let ax = ctrl.get_axis(axis)?;
        let state_info = ax.get_state().await?;
        let status_json = json!({
            "state": format!("{:?}", state_info.state),
            "message": state_info.message,
            "limit_switches": format!("{:?}", state_info.limit_switches),
        });
        let _ = cache.insert(cache_key.clone(), status_json.clone()).await;
        Ok(json!({"controller": controller, "axis": axis, "status": status_json}))
    }

    async fn handle_get_attr(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, Value>,
        controller: &str,
        axis: &str,
        attr: &str,
    ) -> Result<Value> {
        let cache_key = format!("{}::{}::{}", controller, axis, attr);
        if let Some(val) = cache.get(&cache_key).await {
            return Ok(
                json!({"controller": controller, "axis": axis, "attribute": attr, "value": val}),
            );
        }
        // Not in cache or expired: compute
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let value = ctrl.get_attribute(axis, attr).await?;
        let json_value = json!(value);
        // Insert to cache with TTL
        let _ = cache.insert(cache_key.clone(), json_value.clone()).await;
        Ok(json!({"controller": controller, "axis": axis, "attribute": attr, "value": json_value}))
    }

    async fn handle_get_available_params(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let params = ctrl.get_available_attributes(axis).await?;
        Ok(json!({"controller": controller, "axis": axis, "available_params": params}))
    }

    async fn handle_get_supported_movement_params(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let params = ctrl.get_supported_movement_params(axis).await?;
        Ok(json!({"controller": controller, "axis": axis, "supported_movement_params": params}))
    }

    async fn handle_list_controllers(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let controller_names: Vec<String> = ctrls.keys().cloned().collect();
        Ok(json!({"controllers": controller_names}))
    }

    async fn handle_list_axes(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
    ) -> Result<Value> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        let axes = ctrl.axes();
        let axis_names: Vec<String> = axes.iter().map(|ax| ax.name().to_string()).collect();
        Ok(json!({"controller": controller, "axes": axis_names}))
    }
}
