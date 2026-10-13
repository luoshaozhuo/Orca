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
    generation: int = 0
    event_id: str | None = None

    def __post_init__(self) -> None:
        if self.value.quantity != ControlQuantity.ACTIVE_POWER:
            raise ValueError("active-power coordination accepts active power only")
        if self.value.role != ControlValueRole.CONTROL_DEMAND:
            raise ValueError("coordination input must have CONTROL_DEMAND role")
        if self.value.kind != ControlValueKind.DELTA:
            raise ValueError("coordination input must be a delta")
        if self.generation < 0:
            raise ValueError("demand generation must be nonnegative")
        if self.event_id is not None and not self.event_id:
            raise ValueError("event_id cannot be empty")


@dataclass(frozen=True, slots=True)
class CoordinationContext:
    """一次协调周期冻结的基准与有效需求集合。"""

    base: ControlValue
    demands: tuple[ControlDemand, ...]
    base_generation: int = 0

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

        if self.base_generation < 0:
            raise ValueError("base generation must be nonnegative")

        functions = [item.function for item in self.demands]
        if len(functions) != len(set(functions)):
            raise ValueError("duplicate control demand")

        # 同一响应事件只允许一个实际功率贡献，防止内外部重复响应。
        event_ids = [
            item.event_id for item in self.demands if item.event_id is not None
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("same event cannot contribute multiple demands")

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
    base: float = 0.0
    generation: int = 0
    event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("coordination generation must be nonnegative")
