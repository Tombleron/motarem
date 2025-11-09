pub mod limit_switches;
pub mod movement_parameters;
pub mod state;
pub mod state_info;

use movement_parameters::MovementParams;
use state_info::AxisStateInfo;

#[async_trait::async_trait]
pub trait Axis: Send + Sync {
    fn name(&self) -> &str;

    async fn start(&self, target: f64, params: Option<MovementParams>) -> anyhow::Result<()>;
    async fn stop(&self) -> anyhow::Result<()>;

    async fn get_state(&self) -> anyhow::Result<AxisStateInfo>;
    async fn get_attribute(&self, name: &str) -> anyhow::Result<f64>;

    async fn get_position(&self) -> anyhow::Result<f64> {
        self.get_attribute("position").await
    }

    async fn get_available_params(&self) -> anyhow::Result<Vec<String>> {
        Ok(vec!["position".to_string()])
    }

    async fn get_supported_movement_params(&self) -> anyhow::Result<Vec<String>> {
        Ok(vec![
            "velocity".to_string(),
            "acceleration".to_string(),
            "deceleration".to_string(),
        ])
    }
}
