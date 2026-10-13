"""设备功率可行区间及场站分配结果的领域契约。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


@dataclass(frozen=True, slots=True)
class DevicePowerCapability:
    """每一设备每一控制量的当前可用区间；未获授权的设备只能固定输出。"""

    device_id: str
    current: float
    minimum: float
    maximum: float
    controllable: bool = True
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id is required")
        values = (self.current, self.minimum, self.maximum, self.weight)
        if any(isinstance(v, bool) or not math.isfinite(v) for v in values):
            raise ValueError("device capability must be finite")
        if self.minimum > self.maximum or self.weight <= 0:
            raise ValueError("invalid device capability interval or weight")
        if self.controllable and not self.minimum <= self.current <= self.maximum:
            raise ValueError("controllable device current must be feasible")


@dataclass(frozen=True, slots=True)
class PowerAllocationRequest:
    """场站绝对目标及同一测量快照内的设备能力。"""

    target: ControlValue
    devices: tuple[DevicePowerCapability, ...]

    def __post_init__(self) -> None:
        if self.target.role != ControlValueRole.ALLOCATION_TARGET:
            raise ValueError("allocation requires ALLOCATION_TARGET")
        if self.target.kind != ControlValueKind.ABSOLUTE:
            raise ValueError("allocation target must be absolute")
        if self.target.quantity not in (
            ControlQuantity.ACTIVE_POWER,
            ControlQuantity.REACTIVE_POWER,
        ):
            raise ValueError("allocation accepts active or reactive power")
        if self.target.scope not in (ControlScope.POI, ControlScope.PLANT):
            raise ValueError("allocation requires plant or POI target")
        names = [item.device_id for item in self.devices]
        if len(names) != len(set(names)):
            raise ValueError("duplicate device capability")


@dataclass(frozen=True, slots=True)
class PowerAllocationResult:
    """候选设备设定值、不可分配残差及明确的可行状态。"""

    setpoints: tuple[ControlValue, ...]
    fixed_power: float
    allocated_power: float
    residual: float
    feasible: bool
