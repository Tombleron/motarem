pub struct SocketServerConfig {
    pub socket_path: String,
    pub max_connections: usize,
    pub buffer_size: usize,
}

impl Default for SocketServerConfig {
    fn default() -> Self {
        Self {
            socket_path: "/tmp/motarem.sock".to_string(),
            max_connections: 100,
            buffer_size: 8192,
        }
    }
}
