"""可替换的无 I/O 场站控制律。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from core.domain.control_law import ControlLawInput, ControlLawResult
from core.domain.control_value import ControlValue, ControlValueRole


class ControlLaw(Protocol):
    """计算候选目标；不可直接下发设备指令。"""

    def calculate(self, sample: ControlLawInput) -> ControlLawResult:
        """处理一个经验证的控制周期。"""
        ...

    def reset(self) -> None:
        """控制功能退出或状态不连续时重置算法状态。"""
        ...


def _result(sample: ControlLawInput, output: float, error: float) -> ControlLawResult:
    if not math.isfinite(output) or not math.isfinite(error):
        raise ValueError("control law produced a non-finite result")
    candidate = max(sample.minimum, min(sample.maximum, output))
    value = ControlValue(
        quantity=sample.reference.quantity,
        scope=sample.reference.scope,
        target=sample.reference.target,
        role=ControlValueRole.CANDIDATE_TARGET,
        kind=sample.reference.kind,
        value=candidate,
        unit=sample.reference.unit,
    )
    return ControlLawResult(
        candidate=value,
        error=error,
        unsaturated=output,
        saturated=candidate != output,
    )


@dataclass(slots=True)
class DirectControlLaw:
    """同口径参考直传，不提供反馈积分。"""

    def calculate(self, sample: ControlLawInput) -> ControlLawResult:
        return _result(
            sample, sample.reference.value, sample.reference.value - sample.measurement.value
        )

    def reset(self) -> None:
        """直传无状态。"""


@dataclass(slots=True)
class ProportionalControlLaw:
    """按前馈与误差比例项生成绝对候选输出。"""

    kp: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.kp) or self.kp < 0:
            raise ValueError("kp must be finite and nonnegative")

    def calculate(self, sample: ControlLawInput) -> ControlLawResult:
        error = sample.reference.value - sample.measurement.value
        output = sample.feedforward + self.kp * error
        return _result(sample, output, error)

    def reset(self) -> None:
        """比例控制无状态。"""


@dataclass(slots=True)
class PIControlLaw:
    """离散 PI，缺少已验证可实现输出时冻结饱和方向积分。"""

    kp: float
    ki: float
    kaw: float = 0.0
    _integral: float = 0.0
    _previous_error: float | None = None
    _previous_unsaturated: float | None = None
    _previous_saturated: bool = False

    def __post_init__(self) -> None:
        for name, value in (("kp", self.kp), ("ki", self.ki), ("kaw", self.kaw)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")

    def calculate(self, sample: ControlLawInput) -> ControlLawResult:
        error = sample.reference.value - sample.measurement.value
        if self._previous_error is not None:
            delta = self.ki * sample.dt_s * self._previous_error
            if (
                sample.achievable is not None
                and self._previous_unsaturated is not None
            ):
                delta += self.kaw * (
                    sample.achievable - self._previous_unsaturated
                )
            proposed = self._integral + delta
            raw = sample.feedforward + self.kp * error + proposed
            if sample.achievable is None and (
                (raw > sample.maximum and delta > 0)
                or (raw < sample.minimum and delta < 0)
            ):
                proposed = self._integral
            if not math.isfinite(proposed):
                raise ValueError("PI integral overflow")
            self._integral = proposed

        raw = sample.feedforward + self.kp * error + self._integral
        result = _result(sample, raw, error)
        self._previous_error = error
        self._previous_unsaturated = raw
        self._previous_saturated = result.saturated
        return result

    def reset(self) -> None:
        self._integral = 0.0
        self._previous_error = None
        self._previous_unsaturated = None
        self._previous_saturated = False
