"""控制物理量及控制链阶段语义。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ControlQuantity(StrEnum):
    ACTIVE_POWER = "ACTIVE_POWER"
    REACTIVE_POWER = "REACTIVE_POWER"
    APPARENT_POWER = "APPARENT_POWER"
    VOLTAGE = "VOLTAGE"
    CURRENT = "CURRENT"
    FREQUENCY = "FREQUENCY"
    ROCOF = "ROCOF"
    POWER_FACTOR = "POWER_FACTOR"
    ANGLE = "ANGLE"
    SOC = "SOC"
    ENERGY = "ENERGY"


class ControlScope(StrEnum):
    POI = "POI"
    PLANT = "PLANT"
    DEVICE = "DEVICE"


class ControlValueRole(StrEnum):
    MEASUREMENT = "MEASUREMENT"
    REFERENCE = "REFERENCE"
    CONTROL_DEMAND = "CONTROL_DEMAND"
    CANDIDATE_TARGET = "CANDIDATE_TARGET"
    ALLOCATION_TARGET = "ALLOCATION_TARGET"
    DEVICE_SETPOINT = "DEVICE_SETPOINT"


class ControlValueKind(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    DELTA = "DELTA"
    UPPER_LIMIT = "UPPER_LIMIT"
    LOWER_LIMIT = "LOWER_LIMIT"


_UNITS: dict[ControlQuantity, frozenset[str]] = {
    ControlQuantity.ACTIVE_POWER: frozenset({"MW"}),
    ControlQuantity.REACTIVE_POWER: frozenset({"Mvar"}),
    ControlQuantity.APPARENT_POWER: frozenset({"MVA"}),
    ControlQuantity.VOLTAGE: frozenset({"kV", "p.u."}),
    ControlQuantity.CURRENT: frozenset({"A"}),
    ControlQuantity.FREQUENCY: frozenset({"Hz"}),
    ControlQuantity.ROCOF: frozenset({"Hz/s"}),
    ControlQuantity.POWER_FACTOR: frozenset({"1"}),
    ControlQuantity.ANGLE: frozenset({"rad"}),
    ControlQuantity.SOC: frozenset({"%"}),
    ControlQuantity.ENERGY: frozenset({"MWh"}),
}

_POWER_QUANTITIES = frozenset(
    {ControlQuantity.ACTIVE_POWER, ControlQuantity.REACTIVE_POWER}
)


@dataclass(frozen=True, slots=True)
class ControlValue:
    """带物理语义和控制阶段的数值对象。"""

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
        if self.unit not in _UNITS[self.quantity]:
            expected = ", ".join(sorted(_UNITS[self.quantity]))
            raise ValueError(f"expected unit {expected} for {self.quantity}")
        if self.quantity == ControlQuantity.POWER_FACTOR and not (-1 <= self.value <= 1):
            raise ValueError("power factor must be between -1 and 1")
        if self.role == ControlValueRole.DEVICE_SETPOINT:
            if self.scope != ControlScope.DEVICE:
                raise ValueError("device setpoint must target a device")
            if self.quantity not in _POWER_QUANTITIES:
                raise ValueError("device setpoint accepts P or Q only")
            if self.kind != ControlValueKind.ABSOLUTE:
                raise ValueError("device setpoint requires an absolute value")
        if self.role == ControlValueRole.ALLOCATION_TARGET:
            if self.scope not in (ControlScope.POI, ControlScope.PLANT):
                raise ValueError("allocation target must be POI or plant scoped")
            if self.quantity not in _POWER_QUANTITIES:
                raise ValueError("power allocation accepts P or Q only")
            if self.kind != ControlValueKind.ABSOLUTE:
                raise ValueError("allocation target requires an absolute value")
        if self.role == ControlValueRole.CONTROL_DEMAND:
            if self.quantity not in _POWER_QUANTITIES:
                raise ValueError("control demand accepts P or Q only")
            if self.kind != ControlValueKind.DELTA:
                raise ValueError("control demand requires a delta value")


def combine_power_references(
    base: ControlValue,
    *contributions: ControlValue,
) -> ControlValue:
    """组合已授权的同口径功率增量，不执行仲裁或限幅。"""
    if (
        base.role != ControlValueRole.REFERENCE
        or base.kind != ControlValueKind.ABSOLUTE
        or base.quantity not in _POWER_QUANTITIES
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
