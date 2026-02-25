use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AxisState {
    On,
    Moving,
    Alarm,
    Fault,
    Unknown,
}
