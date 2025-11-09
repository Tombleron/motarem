use std::collections::HashMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MovementParams {
    pub velocity: Option<f64>,
    pub acceleration: Option<f64>,
    pub deceleration: Option<f64>,
    pub custom: HashMap<String, f64>,
}

impl MovementParams {
    pub fn new() -> Self {
        Self {
            velocity: None,
            acceleration: None,
            deceleration: None,
            custom: HashMap::new(),
        }
    }

    pub fn with_velocity(mut self, velocity: f64) -> Self {
        self.velocity = Some(velocity);
        self
    }

    pub fn with_acceleration(mut self, acceleration: f64) -> Self {
        self.acceleration = Some(acceleration);
        self
    }

    pub fn with_deceleration(mut self, deceleration: f64) -> Self {
        self.deceleration = Some(deceleration);
        self
    }

    pub fn with_custom_param(mut self, name: String, value: f64) -> Self {
        self.custom.insert(name, value);
        self
    }
}

impl Default for MovementParams {
    fn default() -> Self {
        Self::new()
    }
}
