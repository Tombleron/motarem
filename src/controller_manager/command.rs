use crate::axis::movement_parameters::MovementParams;
use anyhow::Result;
use serde_json::Value;
use tokio::sync::oneshot;

#[derive(Debug)]
pub enum Command {
    Move {
        controller: String,
        axis: String,
        target: f64,
        params: Option<MovementParams>,
        resp: oneshot::Sender<Result<Value>>,
    },
    Stop {
        controller: String,
        axis: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    GetState {
        controller: String,
        axis: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    GetPos {
        controller: String,
        axis: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    GetAttr {
        controller: String,
        axis: String,
        attr: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    GetAvailableParams {
        controller: String,
        axis: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    GetSupportedMovementParams {
        controller: String,
        axis: String,
        resp: oneshot::Sender<Result<Value>>,
    },
    ListControllers {
        resp: oneshot::Sender<Result<Value>>,
    },
    ListAxes {
        controller: String,
        resp: oneshot::Sender<Result<Value>>,
    },
}
