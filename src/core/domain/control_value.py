"""物理控制量及三层控制链的统一语义。

设备输出为候选工程设定值；该模型不授予写入权限。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ControlQuantity(StrEnum):
    ACTIVE_POWER = "ACTIVE_POWER"
    REACTIVE_POWER = "REACTIVE_POWER"
    VOLTAGE = "VOLTAGE"
    POWER_FACTOR = "POWER_FACTOR"
    FREQUENCY = "FREQUENCY"
    ROCOF = "ROCOF"


class ControlScope(StrEnum):
    POI = "POI"
    PLANT = "PLANT"
    DEVICE = "DEVICE"


class ControlValueRole(StrEnum):
    REFERENCE = "REFERENCE"
    ALLOCATION_TARGET = "ALLOCATION_TARGET"
    DEVICE_SETPOINT = "DEVICE_SETPOINT"
    MEASUREMENT = "MEASUREMENT"


class ControlValueKind(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    DELTA = "DELTA"


_UNIT: dict[ControlQuantity, str] = {
    ControlQuantity.ACTIVE_POWER: "MW",
    ControlQuantity.REACTIVE_POWER: "Mvar",
    ControlQuantity.VOLTAGE: "kV",
    ControlQuantity.POWER_FACTOR: "1",
    ControlQuantity.FREQUENCY: "Hz",
    ControlQuantity.ROCOF: "Hz/s",
}


@dataclass(frozen=True, slots=True)
class ControlValue:
    quantity: ControlQuantity
    scope: ControlScope
    target: str
    role: ControlValueRole
    kind: ControlValueKind
    value: float
    unit: str

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("target identifier is required")
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("control value must be finite")
        if self.unit != _UNIT[self.quantity]:
            raise ValueError(f"expected unit {_UNIT[self.quantity]} for {self.quantity}")
        if self.quantity == ControlQuantity.POWER_FACTOR and not (-1 <= self.value <= 1):
            raise ValueError("power factor must be between -1 and 1")
        if self.role == ControlValueRole.DEVICE_SETPOINT and self.scope != ControlScope.DEVICE:
            raise ValueError("device setpoint must target a device")
        if self.role == ControlValueRole.ALLOCATION_TARGET and self.scope != ControlScope.PLANT:
            raise ValueError("allocation target must be plant-scoped")
        if self.role in (
            ControlValueRole.ALLOCATION_TARGET,
            ControlValueRole.DEVICE_SETPOINT,
        ) and self.quantity not in (
            ControlQuantity.ACTIVE_POWER,
            ControlQuantity.REACTIVE_POWER,
        ):
            raise ValueError("power allocation accepts P or Q only")
        if self.role in (
            ControlValueRole.ALLOCATION_TARGET,
            ControlValueRole.DEVICE_SETPOINT,
        ) and self.kind != ControlValueKind.ABSOLUTE:
            raise ValueError("allocation and setpoint require absolute values")


def combine_power_references(
    base: ControlValue, *contributions: ControlValue
) -> ControlValue:
    """组合已由上游授权的功率增量；本函数不执行来源仲裁或限幅。"""
    if (
        base.role != ControlValueRole.REFERENCE
        or base.kind != ControlValueKind.ABSOLUTE
        or base.quantity not in (
            ControlQuantity.ACTIVE_POWER, ControlQuantity.REACTIVE_POWER,
        )
    ):
        raise ValueError("absolute P/Q reference required")
    result = base.value
    for item in contributions:
        if (
            item.role != ControlValueRole.REFERENCE
            or item.kind != ControlValueKind.DELTA
            or item.quantity != base.quantity
            or item.scope != base.scope
            or item.target != base.target
            or item.unit != base.unit
        ):
            raise ValueError("reference contributions have incompatible semantics")
        result += item.value
    if not math.isfinite(result):
        raise ValueError("combined reference must be finite")
    return ControlValue(
        quantity=base.quantity,
        scope=base.scope,
        target=base.target,
        role=ControlValueRole.REFERENCE,
        kind=ControlValueKind.ABSOLUTE,
        value=result,
        unit=base.unit,
    )
