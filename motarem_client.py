import socket
import json
import uuid
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from enum import Enum


class AxisState(Enum):
    """Enumeration of possible axis states."""
    ON = "On"
    MOVING = "Moving"
    ALARM = "Alarm"
    FAULT = "Fault"
    UNKNOWN = "Unknown"

    @classmethod
    def from_str(cls, state_str: str) -> 'AxisState':
        """Create AxisState from string representation."""
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
    def from_str(cls, switch_str: str) -> 'LimitSwitches':
        """Create LimitSwitches from string representation."""
        for switch in cls:
            if switch.value == switch_str:
                return switch
        return cls.NONE

    def has_upper(self) -> bool:
        """Check if upper limit switch is active."""
        return self in (self.UPPER, self.BOTH)

    def has_lower(self) -> bool:
        """Check if lower limit switch is active."""
        return self in (self.LOWER, self.BOTH)

    def is_clear(self) -> bool:
        """Check if no limit switches are active."""
        return self == self.NONE

    def any_active(self) -> bool:
        """Check if any limit switch is active."""
        return not self.is_clear()


class AxisStateInfo:
    """Information about axis state including limit switches and messages."""

    def __init__(self, state: AxisState, message: Optional[str] = None,
                 limit_switches: LimitSwitches = LimitSwitches.NONE):
        self.state = state
        self.message = message
        self.limit_switches = limit_switches

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AxisStateInfo':
        """Create AxisStateInfo from response dictionary."""
        state = AxisState.from_str(data.get("state", "Unknown"))
        message = data.get("message")
        limit_switches = LimitSwitches.from_str(data.get("limit_switches", "None"))
        return cls(state, message, limit_switches)

    def is_moving(self) -> bool:
        """Check if axis is currently moving."""
        return self.state == AxisState.MOVING

    def is_faulted(self) -> bool:
        """Check if axis is in fault or alarm state."""
        return self.state in (AxisState.ALARM, AxisState.FAULT)

    def is_ready(self) -> bool:
        """Check if axis is ready for commands (on and no active limit switches)."""
        return self.state == AxisState.ON and not self.limit_switches.any_active()

    def __repr__(self) -> str:
        return f"AxisStateInfo(state={self.state.value}, message={self.message}, limit_switches={self.limit_switches.value})"


class MovementParams:
    """Parameters for motor movement commands."""

    def __init__(self):
        self.velocity: Optional[float] = None
        self.acceleration: Optional[float] = None
        self.deceleration: Optional[float] = None
        self.custom: Dict[str, float] = {}

    def with_velocity(self, velocity: float) -> 'MovementParams':
        """Set velocity parameter."""
        self.velocity = velocity
        return self

    def with_acceleration(self, acceleration: float) -> 'MovementParams':
        """Set acceleration parameter."""
        self.acceleration = acceleration
        return self

    def with_deceleration(self, deceleration: float) -> 'MovementParams':
        """Set deceleration parameter."""
        self.deceleration = deceleration
        return self

    def with_custom_param(self, name: str, value: float) -> 'MovementParams':
        """Add custom parameter."""
        self.custom[name] = value
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "velocity": self.velocity,
            "acceleration": self.acceleration,
            "deceleration": self.deceleration,
            "custom": self.custom
        }


class MotaremResponse:
    """Response from Motarem server."""

    def __init__(self, raw_response: Dict[str, Any]):
        self.raw = raw_response
        self.status = raw_response.get("status")
        self.id = raw_response.get("id")

        if self.status == "success":
            self.data = raw_response.get("data")
            self.error_message = None
            self.error_code = None
        elif self.status == "error":
            self.data = None
            self.error_message = raw_response.get("message")
            self.error_code = raw_response.get("code")
        else:
            raise ValueError(f"Unknown response status: {self.status}")

    @property
    def is_success(self) -> bool:
        """Check if response indicates success."""
        return self.status == "success"

    @property
    def is_error(self) -> bool:
        """Check if response indicates error."""
        return self.status == "error"

    def __repr__(self) -> str:
        if self.is_success:
            return f"MotaremResponse(success, data={self.data})"
        else:
            return f"MotaremResponse(error, message='{self.error_message}')"


class MotaremException(Exception):
    """Exception raised for Motarem communication errors."""

    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code


class MotaremClient:
    """Client for communicating with Motarem motor controller server."""

    def __init__(self, socket_path: str = "/tmp/motarem.sock"):
        self.socket_path = socket_path
        self.socket = None
        self.socket_file = None

    def connect(self):
        """Establish connection to Motarem server."""
        if self.socket is not None:
            raise ValueError("Already connected")

        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(self.socket_path)
        self.socket_file = self.socket.makefile('rw', encoding='utf-8')

    def disconnect(self):
        """Close connection to Motarem server."""
        if self.socket_file:
            self.socket_file.close()
            self.socket_file = None
        if self.socket:
            self.socket.close()
            self.socket = None

    @contextmanager
    def connection(self):
        """Context manager for automatic connection management."""
        self.connect()
        try:
            yield self
        finally:
            self.disconnect()

    def _send_command(self, command: Dict[str, Any]) -> MotaremResponse:
        """Send command and receive response."""
        if self.socket_file is None:
            raise ValueError("Not connected to server")

        # Serialize and send command
        command_json = json.dumps(command)
        self.socket_file.write(command_json + '\n')
        self.socket_file.flush()

        # Read response
        response_line = self.socket_file.readline().strip()
        if not response_line:
            raise MotaremException("Server closed connection")

        try:
            response_data = json.loads(response_line)
        except json.JSONDecodeError as e:
            raise MotaremException(f"Invalid JSON response: {e}")

        response = MotaremResponse(response_data)

        if response.is_error:
            raise MotaremException(response.error_message, response.error_code)

        return response

    def ping(self) -> Dict[str, Any]:
        """Send ping command to test connection."""
        command = {"type": "ping"}
        response = self._send_command(command)
        return response.data

    def move(self, controller: str, axis: str, target: float,
             params: Optional[MovementParams] = None) -> Dict[str, Any]:
        """Move axis to target position."""
        command = {
            "type": "move",
            "controller": controller,
            "axis": axis,
            "target": target
        }

        if params:
            command["params"] = params.to_dict()

        response = self._send_command(command)
        return response.data

    def stop(self, controller: str, axis: str) -> Dict[str, Any]:
        """Stop axis movement."""
        command = {
            "type": "stop",
            "controller": controller,
            "axis": axis
        }

        response = self._send_command(command)
        return response.data

    def get_controllers(self) -> List[str]:
        """Get list of controller names."""
        command = {"type": "list_controllers"}
        response = self._send_command(command)
        return response.data["controllers"]

    def get_axes(self, controller: str) -> List[str]:
        """Get list of axis names for a controller."""
        command = {
            "type": "list_axes",
            "controller": controller
        }
        response = self._send_command(command)
        return response.data["axes"]

    def get_axis_position(self, controller: str, axis: str) -> float:
        """Get current axis position as float."""
        command = {
            "type": "get_position",
            "controller": controller,
            "axis": axis
        }
        response = self._send_command(command)
        return response.data["position"]

    def get_axis_state_info(self, controller: str, axis: str) -> AxisStateInfo:
        """Get axis state information."""
        command = {
            "type": "get_state",
            "controller": controller,
            "axis": axis
        }
        response = self._send_command(command)
        return AxisStateInfo.from_dict(response.data["state"])

    def get_axis_attribute_value(self, controller: str, axis: str, attribute: str) -> float:
        """Get axis attribute value as float."""
        command = {
            "type": "get_attribute",
            "controller": controller,
            "axis": axis,
            "attribute": attribute
        }
        response = self._send_command(command)
        return response.data["value"]

    def get_axis_available_params(self, controller: str, axis: str) -> List[str]:
        """Get list of available parameter names for axis."""
        command = {
            "type": "get_available_params",
            "controller": controller,
            "axis": axis
        }
        response = self._send_command(command)
        return response.data["available_params"]

    def get_axis_movement_params(self, controller: str, axis: str) -> List[str]:
        """Get list of supported movement parameter names for axis."""
        command = {
            "type": "get_supported_movement_params",
            "controller": controller,
            "axis": axis
        }
        response = self._send_command(command)
        return response.data["supported_movement_params"]


# Example usage
if __name__ == "__main__":
    client = MotaremClient()

    with client.connection():
        # Test ping
        ping_data = client.ping()
        print(f"Ping response: {ping_data}")

        # Get controllers and axes
        controllers = client.get_controllers()
        print(f"Controllers: {controllers}")

        axes = client.get_axes("mock_ctrl_1")
        print(f"Axes: {axes}")

        # Move axis with parameters
        # params = MovementParams().with_velocity(150.0).with_acceleration(2000.0)
        # move_result = client.move("mock_ctrl_1", "X", 100.0, params)
        # print(f"Move response: {move_result}")

        # Get position directly as float
        position = client.get_axis_position("mock_ctrl_1", "X")
        print(f"Position: {position}")

        # Get state info
        state_info = client.get_axis_state_info("mock_ctrl_1", "X")
        print(f"State: {state_info}")
        print(f"Is moving: {state_info.is_moving()}")
        print(f"Is ready: {state_info.is_ready()}")
        print(f"Is faulted: {state_info.is_faulted()}")

        # Get available parameters list
        available_params = client.get_axis_available_params("mock_ctrl_1", "X")
        print(f"Available params: {available_params}")

        velocity = client.get_axis_attribute_value("mock_ctrl_1", "X", "velocity")
        print(f"Velocity: {velocity}")

        # Get movement parameters list
        movement_params = client.get_axis_movement_params("mock_ctrl_1", "X")
        print(f"Movement params: {movement_params}")
