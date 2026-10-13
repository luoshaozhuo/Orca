"""场站控制律的输入、限幅及候选输出契约。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.control_value import (
    ControlQuantity,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


@dataclass(frozen=True, slots=True)
class ControlLawInput:
    """单个被控物理量的标准化参考与有效反馈。"""

    reference: ControlValue
    measurement: ControlValue
    dt_s: float
    minimum: float
    maximum: float
    feedforward: float = 0.0
    achievable: float | None = None

    def __post_init__(self) -> None:
        ref, meas = self.reference, self.measurement
        reference_kinds = {
            ControlValueKind.ABSOLUTE,
            ControlValueKind.UPPER_LIMIT,
            ControlValueKind.LOWER_LIMIT,
        }
        if ref.role != ControlValueRole.REFERENCE or ref.kind not in reference_kinds:
            raise ValueError("control law requires an absolute or limit reference")
        if meas.role != ControlValueRole.MEASUREMENT or meas.kind != ControlValueKind.ABSOLUTE:
            raise ValueError("control law requires an absolute measurement")
        if ref.quantity != meas.quantity or ref.scope != meas.scope:
            raise ValueError("reference and measurement must have the same semantics")
        if ref.target != meas.target or ref.unit != meas.unit:
            raise ValueError("reference and measurement must target the same point")
        if ref.quantity not in (
            ControlQuantity.ACTIVE_POWER,
            ControlQuantity.REACTIVE_POWER,
        ):
            raise ValueError("control law currently supports P and Q")
        values = [self.dt_s, self.minimum, self.maximum, self.feedforward]
        if self.achievable is not None:
            values.append(self.achievable)
        if any(not math.isfinite(item) for item in values):
            raise ValueError("control law inputs must be finite")
        if self.dt_s <= 0 or self.minimum > self.maximum:
            raise ValueError("invalid control law sample time or limits")


@dataclass(frozen=True, slots=True)
class ControlLawResult:
    """不授予设备写入权限的场站候选值。"""

    candidate: ControlValue
    error: float
    unsaturated: float
    saturated: bool

    def __post_init__(self) -> None:
        if (
            self.candidate.role != ControlValueRole.CANDIDATE_TARGET
            or self.candidate.kind != ControlValueKind.ABSOLUTE
        ):
            raise ValueError("control law output must be an absolute candidate")
        if not math.isfinite(self.error) or not math.isfinite(self.unsaturated):
            raise ValueError("control law result must be finite")
