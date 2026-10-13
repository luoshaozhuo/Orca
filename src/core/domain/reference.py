"""控制参考值的类型、来源与确定性仲裁。

只负责参考值语义和选择；控制律与设备指令分配由后续环节执行。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ReferenceSource(StrEnum):
    GRID = "GRID"
    FFR = "FFR"
    THIRD_PARTY = "THIRD_PARTY"
    INTERNAL = "INTERNAL"
    MANUAL = "MANUAL"


class ReferencePurpose(StrEnum):
    ACTIVE_BASE = "ACTIVE_BASE"
    REACTIVE_BASE = "REACTIVE_BASE"
    VOLTAGE_BASE = "VOLTAGE_BASE"
    PFR_DELTA = "PFR_DELTA"
    IR_DELTA = "IR_DELTA"
    REACTIVE_SUPPORT_DELTA = "REACTIVE_SUPPORT_DELTA"


class ValueKind(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    DELTA = "DELTA"


class ArbitrationMode(StrEnum):
    FIXED = "FIXED"
    PRIMARY_BACKUP = "PRIMARY_BACKUP"


_DELTA_PURPOSES = frozenset({
    ReferencePurpose.PFR_DELTA,
    ReferencePurpose.IR_DELTA,
    ReferencePurpose.REACTIVE_SUPPORT_DELTA,
})


@dataclass(frozen=True, slots=True)
class ControlReference:
    purpose: ReferencePurpose
    source: ReferenceSource
    target: str
    kind: ValueKind
    value: float
    timestamp_ns: int
    valid_until_ns: int
    generation: int

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("reference target is required")
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("reference value must be finite")
        if self.timestamp_ns < 0 or self.valid_until_ns <= self.timestamp_ns:
            raise ValueError("invalid reference time interval")
        if self.generation < 0:
            raise ValueError("reference generation must be nonnegative")
        required_kind = (
            ValueKind.DELTA if self.purpose in _DELTA_PURPOSES else ValueKind.ABSOLUTE
        )
        if self.kind != required_kind:
            raise ValueError(f"{self.purpose}: expected {required_kind} reference")

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
    """根据已授权来源选择有效参考。

    主备切换必须经过外部控制权管理器确认，禁止自动隐式回退。
    """
    allowed = (policy.primary,) if policy.mode == ArbitrationMode.FIXED else (
        policy.primary, *policy.backups
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

    for source in allowed:
        if source not in authorized_sources or source not in by_source:
            continue
        if source != policy.primary and source != confirmed_source:
            continue
        return by_source[source]
    return None
