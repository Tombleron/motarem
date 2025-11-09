use std::sync::Arc;

use crate::axis::{movement_parameters::MovementParams, state_info::AxisStateInfo, Axis};

#[async_trait::async_trait]
pub trait MotorController: Send + Sync {
    fn name(&self) -> &str;

    fn axes(&self) -> Vec<Arc<dyn Axis>>;
    fn get_axis(&self, axis: &str) -> anyhow::Result<Arc<dyn Axis>> {
        self.axes()
            .iter()
            .find(|a| a.name() == axis)
            .ok_or_else(|| {
                anyhow::anyhow!("Axis not found: {} in controller {}", axis, self.name())
            })
            .cloned()
    }

    async fn shutdown(&self) -> anyhow::Result<()> {
        for axis in self.axes() {
            axis.stop().await?;
        }
        Ok(())
    }

    async fn start(
        &self,
        axis: &str,
        target: f64,
        params: Option<MovementParams>,
    ) -> anyhow::Result<()> {
        let ax = self.get_axis(axis)?;
        ax.start(target, params).await
    }

    async fn stop(&self, axis: &str) -> anyhow::Result<()> {
        let ax = self.get_axis(axis)?;
        ax.stop().await
    }

    async fn state(&self, axis: &str) -> anyhow::Result<AxisStateInfo> {
        let ax = self.get_axis(axis)?;
        ax.get_state().await
    }

    async fn get_attribute(&self, axis: &str, attribute: &str) -> anyhow::Result<f64> {
        let supported_attributes = self.get_available_attributes(axis).await?;
        if !supported_attributes.contains(&attribute.to_string()) {
            return Err(anyhow::anyhow!("Attribute not supported: {}", attribute));
        }

        let ax = self.get_axis(axis)?;
        ax.get_attribute(attribute).await
    }

    async fn get_available_attributes(&self, axis: &str) -> anyhow::Result<Vec<String>> {
        let ax = self.get_axis(axis)?;
        ax.get_available_params().await
    }

    async fn get_supported_movement_params(&self, axis: &str) -> anyhow::Result<Vec<String>> {
        let ax = self.get_axis(axis)?;
        ax.get_supported_movement_params().await
    }
}
