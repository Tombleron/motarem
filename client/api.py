import json
import socket
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, IO, List, Optional, TypeVar


class AxisState(Enum):
    """Enumeration of possible axis states."""

    ON = "On"
    MOVING = "Moving"
    ALARM = "Alarm"
    FAULT = "Fault"
    UNKNOWN = "Unknown"

    @classmethod
    def from_str(cls, state_str: str) -> "AxisState":
        for state in cls:
            if state.value == state_str:
                return state
        return cls.UNKNOWN


class LimitSwitches(Enum):
    """Enumeration of limit switch states."""

    NONE = "None"
    UPPER = "Upper"
    LOWER = "Lower"
    BOTH = "Both"

    @classmethod
    def from_str(cls, switch_str: str) -> "LimitSwitches":
        for switch in cls:
            if switch.value == switch_str:
                return switch
        return cls.NONE

    def has_upper(self) -> bool:
        return self in (self.UPPER, self.BOTH)

    def has_lower(self) -> bool:
        return self in (self.LOWER, self.BOTH)

    def is_clear(self) -> bool:
        return self == self.NONE

    def any_active(self) -> bool:
        return not self.is_clear()


@dataclass(frozen=True, slots=True)
class AxisStateInfo:
    """Information about axis state including limit switches and messages."""

    state: AxisState
    message: Optional[str] = None
    limit_switches: LimitSwitches = LimitSwitches.NONE

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AxisStateInfo":
        return cls(
            state=AxisState.from_str(data.get("state", "Unknown")),
            message=data.get("message"),
            limit_switches=LimitSwitches.from_str(
                data.get("limit_switches", "None")
            ),
        )

    def is_moving(self) -> bool:
        return self.state == AxisState.MOVING

    def is_faulted(self) -> bool:
        return self.state in (AxisState.ALARM, AxisState.FAULT)

    def is_ready(self) -> bool:
        return self.state == AxisState.ON and not self.limit_switches.any_active()


@dataclass(frozen=True, slots=True)
class MovementParams:
    """Parameters for motor movement commands."""

    velocity: Optional[float] = None
    acceleration: Optional[float] = None
    deceleration: Optional[float] = None
    custom: Dict[str, float] = field(default_factory=dict)

    def with_velocity(self, velocity: float) -> "MovementParams":
        return replace(self, velocity=velocity)

    def with_acceleration(self, acceleration: float) -> "MovementParams":
        return replace(self, acceleration=acceleration)

    def with_deceleration(self, deceleration: float) -> "MovementParams":
        return replace(self, deceleration=deceleration)

    def with_custom_param(self, name: str, value: float) -> "MovementParams":
        custom = dict(self.custom)
        custom[name] = value
        return replace(self, custom=custom)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "velocity": self.velocity,
            "acceleration": self.acceleration,
            "deceleration": self.deceleration,
            "custom": self.custom,
        }


@dataclass(frozen=True, slots=True)
class Command:
    """Serializable client command envelope."""

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        command = {"type": self.type, **self.payload}
        if self.id is not None:
            command["id"] = self.id
        return command


@dataclass(frozen=True, slots=True)
class MotaremResponse:
    """Parsed response envelope from Motarem server."""

    status: str
    id: Optional[str]
    data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw_response: Dict[str, Any]) -> "MotaremResponse":
        status = raw_response.get("status")
        response_id = raw_response.get("id")

        if status == "success":
            data = raw_response.get("data")
            if not isinstance(data, dict):
                raise MotaremException("Invalid success response: missing object data")
            return cls(status=status, id=response_id, data=data, raw=raw_response)

        if status == "error":
            return cls(
                status=status,
                id=response_id,
                error_message=raw_response.get("message", "Unknown server error"),
                error_code=raw_response.get("code"),
                raw=raw_response,
            )

        raise MotaremException(f"Unknown response status: {status}")

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_error(self) -> bool:
        return self.status == "error"


class MotaremException(Exception):
    """Exception raised for Motarem communication errors."""

    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code


T = TypeVar("T")


class MotaremClient:
    """Client for communicating with Motarem motor controller server."""

    def __init__(self, socket_path: str = "/tmp/motarem.sock"):
        self.socket_path = socket_path
        self.socket: Optional[socket.socket] = None
        self.socket_file: Optional[IO[str]] = None

    def connect(self) -> "MotaremClient":
        if self.socket is not None:
            raise ValueError("Already connected")

        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(self.socket_path)
        self.socket_file = self.socket.makefile("rw", encoding="utf-8")
        return self

    def disconnect(self) -> None:
        if self.socket_file is not None:
            self.socket_file.close()
            self.socket_file = None
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def __enter__(self) -> "MotaremClient":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    @contextmanager
    def connection(self):
        with self:
            yield self

    def _next_request_id(self) -> str:
        return uuid.uuid4().hex

    def _require_connection(self) -> IO[str]:
        if self.socket_file is None:
            raise ValueError("Not connected to server")
        return self.socket_file

    def _write_command(self, command: Command) -> None:
        socket_file = self._require_connection()
        try:
            socket_file.write(json.dumps(command.to_dict()) + "\n")
            socket_file.flush()
        except OSError as exc:
            raise MotaremException(f"Failed to send command: {exc}") from exc

    def _read_response(self) -> MotaremResponse:
        socket_file = self._require_connection()
        try:
            response_line = socket_file.readline()
        except OSError as exc:
            raise MotaremException(f"Failed to read response: {exc}") from exc

        if not response_line:
            raise MotaremException("Server closed connection")

        try:
            response_data = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise MotaremException(f"Invalid JSON response: {exc}") from exc

        if not isinstance(response_data, dict):
            raise MotaremException("Invalid response payload: expected object")

        return MotaremResponse.from_dict(response_data)

    def _send_command(self, command_type: str, **payload: Any) -> Dict[str, Any]:
        command = Command(
            type=command_type,
            payload=payload,
            id=self._next_request_id(),
        )
        self._write_command(command)
        response = self._read_response()

        if response.id is not None and response.id != command.id:
            raise MotaremException(
                f"Response id mismatch: expected {command.id}, got {response.id}"
            )

        if response.is_error:
            raise MotaremException(
                response.error_message or "Unknown error",
                response.error_code,
            )

        return response.data or {}

    def _request(
        self,
        command_type: str,
        parser: Callable[[Dict[str, Any]], T],
        **payload: Any,
    ) -> T:
        data = self._send_command(command_type, **payload)
        return parser(data)

    def ping(self) -> Dict[str, Any]:
        return self._request("ping", lambda data: data)

    def move(
        self,
        controller: str,
        axis: str,
        target: float,
        params: Optional[MovementParams] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "controller": controller,
            "axis": axis,
            "target": target,
        }
        if params is not None:
            payload["params"] = params.to_dict()
        return self._request("move", lambda data: data, **payload)

    def stop(self, controller: str, axis: str) -> Dict[str, Any]:
        return self._request(
            "stop",
            lambda data: data,
            controller=controller,
            axis=axis,
        )

    def get_controllers(self) -> List[str]:
        return self._request(
            "list_controllers",
            lambda data: self._required_list(data, "controllers"),
        )

    def get_axes(self, controller: str) -> List[str]:
        return self._request(
            "list_axes",
            lambda data: self._required_list(data, "axes"),
            controller=controller,
        )

    def get_axis_position(self, controller: str, axis: str) -> float:
        return self._request(
            "get_position",
            lambda data: self._required_float(data, "position"),
            controller=controller,
            axis=axis,
        )

    def get_axis_state_info(self, controller: str, axis: str) -> AxisStateInfo:
        return self._request(
            "get_state",
            lambda data: AxisStateInfo.from_dict(self._required_dict(data, "state")),
            controller=controller,
            axis=axis,
        )

    def get_axis_attribute_value(
        self, controller: str, axis: str, attribute: str
    ) -> float:
        return self._request(
            "get_attribute",
            lambda data: self._required_float(data, "value"),
            controller=controller,
            axis=axis,
            attribute=attribute,
        )

    def get_axis_available_params(self, controller: str, axis: str) -> List[str]:
        return self._request(
            "get_available_params",
            lambda data: self._required_list(data, "available_params"),
            controller=controller,
            axis=axis,
        )

    def get_axis_movement_params(self, controller: str, axis: str) -> List[str]:
        return self._request(
            "get_supported_movement_params",
            lambda data: self._required_list(data, "supported_movement_params"),
            controller=controller,
            axis=axis,
        )

    @staticmethod
    def _required_dict(data: Dict[str, Any], key: str) -> Dict[str, Any]:
        value = data.get(key)
        if not isinstance(value, dict):
            raise MotaremException(f"Invalid response: missing object field '{key}'")
        return value

    @staticmethod
    def _required_list(data: Dict[str, Any], key: str) -> List[str]:
        value = data.get(key)
        if not isinstance(value, list):
            raise MotaremException(f"Invalid response: missing list field '{key}'")
        return value

    @staticmethod
    def _required_float(data: Dict[str, Any], key: str) -> float:
        value = data.get(key)
        if not isinstance(value, (int, float)):
            raise MotaremException(f"Invalid response: missing numeric field '{key}'")
        return float(value)


if __name__ == "__main__":
    with MotaremClient() as client:
        print(f"Ping response: {client.ping()}")
        print(f"Controllers: {client.get_controllers()}")

        axes = client.get_axes("mock_ctrl_1")
        print(f"Axes: {axes}")

        position = client.get_axis_position("mock_ctrl_1", "X")
        print(f"Position: {position}")

        state_info = client.get_axis_state_info("mock_ctrl_1", "X")
        print(f"State: {state_info}")
        print(f"Is moving: {state_info.is_moving()}")
        print(f"Is ready: {state_info.is_ready()}")
        print(f"Is faulted: {state_info.is_faulted()}")

        available_params = client.get_axis_available_params("mock_ctrl_1", "X")
        print(f"Available params: {available_params}")

        velocity = client.get_axis_attribute_value("mock_ctrl_1", "X", "velocity")
        print(f"Velocity: {velocity}")

        movement_params = client.get_axis_movement_params("mock_ctrl_1", "X")
        print(f"Movement params: {movement_params}")
