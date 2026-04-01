from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True, slots=True)
class AxisData:
    available_params: List[str] = field(default_factory=list)
    movement_params: List[str] = field(default_factory=list)
    param_values: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ControllerData:
    axes: List[str] = field(default_factory=list)
    axis_data: Dict[str, AxisData] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AxisSnapshot:
    position: float
    state: str
    message: Optional[str]
    limit_switches: str
    is_moving: bool
    is_ready: bool
    is_faulted: bool
