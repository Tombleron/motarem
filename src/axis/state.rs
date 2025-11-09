#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AxisState {
    On,
    Moving,
    Alarm,
    Fault,
    Unknown,
}
