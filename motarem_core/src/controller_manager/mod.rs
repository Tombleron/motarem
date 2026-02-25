pub mod command;
pub mod config;

use command::Command;
use config::ManagerConfig;

use anyhow::Result;
use moka::future::Cache;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::{mpsc, RwLock};

use crate::{
    axis::movement_parameters::MovementParams, controller_manager::command::MotaremResponse,
    motor_controller::MotorController,
};

pub struct ControllerManager {
    controllers: Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
    cmd_sender: mpsc::Sender<Command>,
    cache: Cache<String, MotaremResponse>,
    config: ManagerConfig,
}

impl ControllerManager {
    pub fn new(config: ManagerConfig) -> Self {
        let cache = Cache::builder()
            .max_capacity(config.cache_capacity as u64)
            .time_to_live(config.default_ttl)
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

    pub fn cache(&self) -> &Cache<String, MotaremResponse> {
        &self.cache
    }

    pub fn config(&self) -> &ManagerConfig {
        &self.config
    }

    async fn command_loop(
        controllers: Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: Cache<String, MotaremResponse>,
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
        cache: &Cache<String, MotaremResponse>,
        controller: &str,
        axis: &str,
        target: f64,
        params: Option<MovementParams>,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        ctrl.start(axis, target, params).await?;

        let cache_key = format!("{}::{}::position", controller, axis);
        cache.invalidate(&cache_key).await;
        Ok(MotaremResponse::Move { target })
    }

    async fn handle_stop(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        ctrl.stop(axis).await?;
        Ok(MotaremResponse::Stop)
    }

    async fn handle_get_pos(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, MotaremResponse>,
        controller: &str,
        axis: &str,
    ) -> Result<MotaremResponse> {
        let cache_key = format!("{}::{}::position", controller, axis);

        if let Some(val) = cache.get(&cache_key).await {
            return Ok(val);
        }

        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        let ax = ctrl.get_axis(axis)?;

        let pos = ax.get_position().await?;

        let value = MotaremResponse::Pos {
            controller: controller.to_string(),
            axis: axis.to_string(),
            position: pos,
        };

        let _ = cache.insert(cache_key.clone(), value.clone()).await;

        Ok(value)
    }

    async fn handle_get_state(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, MotaremResponse>,
        controller: &str,
        axis: &str,
    ) -> Result<MotaremResponse> {
        let cache_key = format!("{}::{}::status", controller, axis);
        if let Some(val) = cache.get(&cache_key).await {
            return Ok(val);
        }
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let ax = ctrl.get_axis(axis)?;
        let state_info = ax.get_state().await?;

        let value = MotaremResponse::State {
            controller: controller.to_string(),
            axis: axis.to_string(),
            state: state_info,
        };

        let _ = cache.insert(cache_key.clone(), value.clone()).await;
        Ok(value)
    }

    async fn handle_get_attr(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        cache: &Cache<String, MotaremResponse>,
        controller: &str,
        axis: &str,
        attr: &str,
    ) -> Result<MotaremResponse> {
        let cache_key = format!("{}::{}::{}", controller, axis, attr);
        if let Some(val) = cache.get(&cache_key).await {
            return Ok(val);
        }
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let value = ctrl.get_attribute(axis, attr).await?;

        let value = MotaremResponse::Attribute {
            controller: controller.to_string(),
            axis: axis.to_string(),
            attribute: attr.to_string(),
            value,
        };

        let _ = cache.insert(cache_key.clone(), value.clone()).await;

        Ok(value)
    }

    async fn handle_get_available_params(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let params = ctrl.get_available_attributes(axis).await?;

        let response = MotaremResponse::AvailableParams {
            controller: controller.to_string(),
            axis: axis.to_string(),
            available_params: params,
        };

        Ok(response)
    }

    async fn handle_get_supported_movement_params(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
        axis: &str,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;
        let params = ctrl.get_supported_movement_params(axis).await?;
        Ok(MotaremResponse::SupportedMovementParams {
            controller: controller.to_string(),
            axis: axis.to_string(),
            supported_movement_params: params,
        })
    }

    async fn handle_list_controllers(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let controller_names: Vec<String> = ctrls.keys().cloned().collect();
        Ok(MotaremResponse::ListControllers {
            controllers: controller_names,
        })
    }

    async fn handle_list_axes(
        controllers: &Arc<RwLock<HashMap<String, Arc<dyn MotorController>>>>,
        controller: &str,
    ) -> Result<MotaremResponse> {
        let ctrls = controllers.read().await;
        let ctrl = ctrls
            .get(controller)
            .ok_or_else(|| anyhow::anyhow!("Controller not found: {}", controller))?;

        let axes = ctrl.axes();
        let axis_names: Vec<String> = axes.iter().map(|ax| ax.name().to_string()).collect();
        Ok(MotaremResponse::ListAxes {
            controller: controller.to_string(),
            axes: axis_names,
        })
    }
}
