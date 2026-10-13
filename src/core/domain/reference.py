"""控制参考值的来源、用途和确定性仲裁。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.domain.control_value import (
    ControlQuantity,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


class ReferenceSource(StrEnum):
    GRID = "GRID"
    FFR = "FFR"
    THIRD_PARTY = "THIRD_PARTY"
    INTERNAL = "INTERNAL"
    MANUAL = "MANUAL"


class ReferencePurpose(StrEnum):
    ACTIVE_BASE = "ACTIVE_BASE"
    ACTIVE_LIMIT = "ACTIVE_LIMIT"
    REACTIVE_BASE = "REACTIVE_BASE"
    VOLTAGE_BASE = "VOLTAGE_BASE"
    POWER_FACTOR = "POWER_FACTOR"
    PFR_DELTA = "PFR_DELTA"
    IR_DELTA = "IR_DELTA"
    VOLTAGE_SUPPORT_DELTA = "VOLTAGE_SUPPORT_DELTA"


class ArbitrationMode(StrEnum):
    FIXED = "FIXED"
    PRIMARY_BACKUP = "PRIMARY_BACKUP"


_PURPOSE_CONTRACT: dict[
    ReferencePurpose,
    tuple[ControlQuantity, frozenset[ControlValueKind]],
] = {
    ReferencePurpose.ACTIVE_BASE: (
        ControlQuantity.ACTIVE_POWER,
        frozenset({ControlValueKind.ABSOLUTE}),
    ),
    ReferencePurpose.ACTIVE_LIMIT: (
        ControlQuantity.ACTIVE_POWER,
        frozenset({ControlValueKind.UPPER_LIMIT, ControlValueKind.LOWER_LIMIT}),
    ),
    ReferencePurpose.REACTIVE_BASE: (
        ControlQuantity.REACTIVE_POWER,
        frozenset({ControlValueKind.ABSOLUTE}),
    ),
    ReferencePurpose.VOLTAGE_BASE: (
        ControlQuantity.VOLTAGE,
        frozenset({ControlValueKind.ABSOLUTE}),
    ),
    ReferencePurpose.POWER_FACTOR: (
        ControlQuantity.POWER_FACTOR,
        frozenset({ControlValueKind.ABSOLUTE}),
    ),
    ReferencePurpose.PFR_DELTA: (
        ControlQuantity.ACTIVE_POWER,
        frozenset({ControlValueKind.DELTA}),
    ),
    ReferencePurpose.IR_DELTA: (
        ControlQuantity.ACTIVE_POWER,
        frozenset({ControlValueKind.DELTA}),
    ),
    ReferencePurpose.VOLTAGE_SUPPORT_DELTA: (
        ControlQuantity.REACTIVE_POWER,
        frozenset({ControlValueKind.DELTA}),
    ),
}


@dataclass(frozen=True, slots=True)
class ControlReference:
    """参考用途与统一控制量的组合，不重复建模物理语义。"""

    purpose: ReferencePurpose
    source: ReferenceSource
    control: ControlValue
    timestamp_ns: int
    valid_until_ns: int
    generation: int
    sequence_id: int = 0
    event_id: str | None = None

    def __post_init__(self) -> None:
        if self.control.role != ControlValueRole.REFERENCE:
            raise ValueError("reference control value must have REFERENCE role")
        expected_quantity, allowed_kinds = _PURPOSE_CONTRACT[self.purpose]
        if self.control.quantity != expected_quantity:
            raise ValueError(
                f"{self.purpose}: expected quantity {expected_quantity}"
            )
        if self.control.kind not in allowed_kinds:
            allowed = ", ".join(sorted(item.value for item in allowed_kinds))
            raise ValueError(f"{self.purpose}: expected kind {allowed}")
        if self.timestamp_ns < 0 or self.valid_until_ns <= self.timestamp_ns:
            raise ValueError("invalid reference time interval")
        if self.generation < 0 or self.sequence_id < 0:
            raise ValueError("reference generation and sequence must be nonnegative")
        if self.event_id is not None and not self.event_id:
            raise ValueError("event_id cannot be empty")

    @property
    def target(self) -> str:
        return self.control.target

    def is_valid_at(self, now_ns: int) -> bool:
        return self.timestamp_ns <= now_ns < self.valid_until_ns


@dataclass(frozen=True, slots=True)
class ReferenceSelection:
    purpose: ReferencePurpose
    target: str
    mode: ArbitrationMode
    primary: ReferenceSource
    backups: tuple[ReferenceSource, ...] = ()

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("reference target is required")
        if self.primary in self.backups or len(set(self.backups)) != len(self.backups):
            raise ValueError("duplicate reference source")
        if self.mode == ArbitrationMode.FIXED and self.backups:
            raise ValueError("FIXED arbitration cannot have backups")


def select_reference(
    policy: ReferenceSelection,
    candidates: tuple[ControlReference, ...],
    *,
    now_ns: int,
    authorized_sources: frozenset[ReferenceSource],
    confirmed_source: ReferenceSource | None = None,
) -> ControlReference | None:
    """根据显式授权和已确认来源选择当前有效参考。"""
    allowed = (
        (policy.primary,)
        if policy.mode == ArbitrationMode.FIXED
        else (policy.primary, *policy.backups)
    )
    by_source: dict[ReferenceSource, ControlReference] = {}
    for candidate in candidates:
        if candidate.purpose != policy.purpose or candidate.target != policy.target:
            continue
        if candidate.source not in allowed or not candidate.is_valid_at(now_ns):
            continue
        if candidate.source in by_source:
            raise ValueError(f"multiple active references from {candidate.source}")
        by_source[candidate.source] = candidate

    selected = confirmed_source if confirmed_source is not None else policy.primary
    if selected not in allowed or selected not in authorized_sources:
        return None
    return by_source.get(selected)
