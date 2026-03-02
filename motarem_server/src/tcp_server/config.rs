pub struct TcpServerConfig {
    pub bind_addr: String,
    pub max_connections: usize,
    pub buffer_size: usize,
}

impl Default for TcpServerConfig {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:7878".to_string(),
            max_connections: 100,
            buffer_size: 8192,
        }
    }
}
