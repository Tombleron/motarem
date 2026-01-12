use std::fmt::Display;

use crate::axis::{movement_parameters::MovementParams, state_info::AxisStateInfo};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::sync::oneshot;

type ResponseChannel = oneshot::Sender<Result<MotaremResponse>>;

#[derive(Debug)]
pub enum Command {
    Move {
        controller: String,
        axis: String,
        target: f64,
        params: Option<MovementParams>,
        resp: ResponseChannel,
    },
    Stop {
        controller: String,
        axis: String,
        resp: ResponseChannel,
    },
    GetState {
        controller: String,
        axis: String,
        resp: ResponseChannel,
    },
    GetPos {
        controller: String,
        axis: String,
        resp: ResponseChannel,
    },
    GetAttr {
        controller: String,
        axis: String,
        attr: String,
        resp: ResponseChannel,
    },
    GetAvailableParams {
        controller: String,
        axis: String,
        resp: ResponseChannel,
    },
    GetSupportedMovementParams {
        controller: String,
        axis: String,
        resp: ResponseChannel,
    },
    ListControllers {
        resp: ResponseChannel,
    },
    ListAxes {
        controller: String,
        resp: ResponseChannel,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum MotaremResponse {
    Move {
        target: f64,
    },
    Stop,
    Pos {
        controller: String,
        axis: String,
        position: f64,
    },
    State {
        controller: String,
        axis: String,
        #[serde(rename = "state")]
        state: AxisStateInfo,
    },
    Attribute {
        controller: String,
        axis: String,
        attribute: String,
        value: f64,
    },
    AvailableParams {
        controller: String,
        axis: String,
        available_params: Vec<String>,
    },
    SupportedMovementParams {
        controller: String,
        axis: String,
        supported_movement_params: Vec<String>,
    },
    ListControllers {
        controllers: Vec<String>,
    },
    ListAxes {
        controller: String,
        axes: Vec<String>,
    },
    Pong,
}

impl Display for MotaremResponse {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MotaremResponse::Move { target } => write!(f, "Moving to {}", target),
            MotaremResponse::Stop => write!(f, "Stopping"),
            MotaremResponse::Pos {
                controller,
                axis,
                position,
            } => write!(f, "Position of {} on {} is {}", axis, controller, position),
            MotaremResponse::State {
                controller,
                axis,
                state,
            } => write!(f, "State of {} on {} is {:?}", axis, controller, state),
            MotaremResponse::Attribute {
                controller,
                axis,
                attribute,
                value,
            } => write!(
                f,
                "Attribute {} of {} on {} is {}",
                attribute, axis, controller, value
            ),
            MotaremResponse::AvailableParams {
                controller,
                axis,
                available_params,
            } => write!(
                f,
                "Available parameters for {} on {} are {:?}",
                axis, controller, available_params
            ),
            MotaremResponse::SupportedMovementParams {
                controller,
                axis,
                supported_movement_params,
            } => write!(
                f,
                "Supported movement parameters for {} on {} are {:?}",
                axis, controller, supported_movement_params
            ),
            MotaremResponse::ListControllers { controllers } => {
                write!(f, "Controllers: {:?}", controllers)
            }
            MotaremResponse::ListAxes { controller, axes } => {
                write!(f, "Axes of {} are {:?}", controller, axes)
            }
            MotaremResponse::Pong => {
                write!(f, "Pong")
            }
        }
    }
}
