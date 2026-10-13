"""多控制需求协调的领域契约。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.domain.control_value import (
    ControlQuantity,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


class CoordinationFunction(StrEnum):
    AGC = "agc"
    PFR = "pfr"
    IR = "ir"


class CoordinationAlgorithm(StrEnum):
    DEFAULT = "default"
    SUM = "sum"
    PRIORITY = "priority"
    EXCLUSIVE = "exclusive"
    DIRECTIONAL = "directional"


class TieBreak(StrEnum):
    REJECT = "reject"
    SUM = "sum"
    FIXED_ORDER = "fixed_order"


class ExclusiveSelect(StrEnum):
    HIGHEST_PRIORITY = "highest_priority"
    FIXED_ORDER = "fixed_order"


class DirectionResolution(StrEnum):
    SUM = "sum"
    PRIORITY = "priority"
    SUPPRESS = "suppress"


class PfrAgcMode(StrEnum):
    SAME_DIRECTION_ADD = "same_direction_add"


@dataclass(frozen=True, slots=True)
class ControlDemand:
    """统一基准上的单个有功控制需求增量。"""

    function: CoordinationFunction
    value: ControlValue

    def __post_init__(self) -> None:
        if self.value.quantity != ControlQuantity.ACTIVE_POWER:
            raise ValueError("active-power coordination accepts active power only")
        if self.value.role != ControlValueRole.CONTROL_DEMAND:
            raise ValueError("coordination input must have CONTROL_DEMAND role")
        if self.value.kind != ControlValueKind.DELTA:
            raise ValueError("coordination input must be a delta")


@dataclass(frozen=True, slots=True)
class CoordinationContext:
    """一次协调周期冻结的基准与有效需求集合。"""

    base: ControlValue
    demands: tuple[ControlDemand, ...]

    def __post_init__(self) -> None:
        if self.base.quantity != ControlQuantity.ACTIVE_POWER:
            raise ValueError("coordination base must be active power")
        if self.base.kind != ControlValueKind.ABSOLUTE:
            raise ValueError("coordination base must be absolute")
        if self.base.role not in (
            ControlValueRole.REFERENCE,
            ControlValueRole.CANDIDATE_TARGET,
        ):
            raise ValueError("invalid coordination base role")

        functions = [item.function for item in self.demands]
        if len(functions) != len(set(functions)):
            raise ValueError("duplicate control demand")

        for demand in self.demands:
            value = demand.value
            if (
                value.scope != self.base.scope
                or value.target != self.base.target
                or value.unit != self.base.unit
            ):
                raise ValueError("control demands use incompatible power semantics")


@dataclass(frozen=True, slots=True)
class ContributionDecision:
    """单个控制需求在本周期的采纳结果。"""

    function: CoordinationFunction
    requested: float
    accepted: float
    reason: str


@dataclass(frozen=True, slots=True)
class CoordinationResult:
    """协调后的绝对场站目标及逐需求诊断。"""

    target: ControlValue
    dp_coord: float
    algorithm: CoordinationAlgorithm
    matched_rule: tuple[CoordinationFunction, ...] | None
    contributions: tuple[ContributionDecision, ...]
