use super::limit_switches::LimitSwitches;
use super::state::AxisState;

#[derive(Debug, Clone)]
pub struct AxisStateInfo {
    pub state: AxisState,
    pub message: Option<String>,
    pub limit_switches: LimitSwitches,
}

impl AxisStateInfo {
    pub fn new(state: AxisState) -> Self {
        Self {
            state,
            message: None,
            limit_switches: LimitSwitches::None,
        }
    }

    pub fn with_message(mut self, message: String) -> Self {
        self.message = Some(message);
        self
    }

    pub fn with_limit_switches(mut self, limit_switches: LimitSwitches) -> Self {
        self.limit_switches = limit_switches;
        self
    }

    pub fn is_moving(&self) -> bool {
        self.state == AxisState::Moving
    }

    pub fn is_faulted(&self) -> bool {
        matches!(self.state, AxisState::Alarm | AxisState::Fault)
    }

    pub fn is_ready(&self) -> bool {
        self.state == AxisState::On && !self.limit_switches.any_active()
    }
}
