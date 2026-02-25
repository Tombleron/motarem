use std::time::Duration;

pub struct ManagerConfig {
    pub default_ttl: Duration,
    pub cache_capacity: usize,
}
