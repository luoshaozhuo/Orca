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
            if not math.isfinite(delta):
                raise ValueError("PI integral overflow")
            if (
                sample.achievable is not None
                and self._previous_unsaturated is not None
            ):
                delta += self.kaw * (
                    sample.achievable - self._previous_unsaturated
                )
            proposed = self._integral + delta
            if not math.isfinite(proposed):
                raise ValueError("PI integral overflow")
            raw = sample.feedforward + self.kp * error + proposed
            if not math.isfinite(raw):
                raise ValueError("PI control output overflow")
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


@dataclass(slots=True)
class VoltageReactiveLaw:
    """AVC 定电压模式：电压偏差经 PI 产生无功修正量。

    输出为并网点无功绝对候选值（Mvar），在当前无功实测基础上叠加
    比例与积分修正；积分在饱和方向上冻结，替代缺失的可实现输出回算。
    增益单位为 Mvar/kV 与 Mvar/(kV·s)，符号约定：电压低于参考时
    产生正无功（向电网注入）。
    """

    kp: float
    ki: float
    _integral: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.kp) or not math.isfinite(self.ki):
            raise ValueError("voltage law gains must be finite")
        if self.kp < 0 or self.ki < 0:
            raise ValueError("voltage law gains must be nonnegative")

    def calculate(
        self,
        u_ref: float,
        u_meas: float,
        q_meas: float,
        dt_s: float,
        minimum: float,
        maximum: float,
    ) -> float:
        """返回限幅后的无功候选值；任何非有限输入均拒绝。"""
        values = (u_ref, u_meas, q_meas, dt_s, minimum, maximum)
        if any(not math.isfinite(item) for item in values):
            raise ValueError("voltage law inputs must be finite")
        if dt_s <= 0 or minimum > maximum:
            raise ValueError("invalid voltage law sample period or limits")
        error = u_ref - u_meas
        proposed = self._integral + self.ki * dt_s * error
        if not math.isfinite(proposed):
            raise ValueError("voltage law integral overflow")
        raw = q_meas + self.kp * error + proposed
        if (raw > maximum and proposed > self._integral) or (
            raw < minimum and proposed < self._integral
        ):
            proposed = self._integral
        self._integral = proposed
        output = q_meas + self.kp * error + self._integral
        return max(minimum, min(maximum, output))

    def reset(self) -> None:
        self._integral = 0.0
