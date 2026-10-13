"""单变量反馈信号调理：无 I/O 的确定性滤波。"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class PassthroughSignal:
    """无延迟反馈直通。"""

    def step(self, measurement: float, dt_s: float) -> float:
        if not math.isfinite(measurement) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("invalid measurement or sample period")
        return measurement

    def reset(self) -> None:
        """直通无状态。"""


@dataclass(slots=True)
class LowPassSignal:
    """时间常数可配置的一阶反馈低通滤波。"""

    tau_s: float
    _previous: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.tau_s) or self.tau_s <= 0:
            raise ValueError("filter time constant must be finite and positive")

    def step(self, measurement: float, dt_s: float) -> float:
        if not math.isfinite(measurement) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("invalid measurement or sample period")
        if self._previous is None:
            self._previous = measurement
        else:
            alpha = dt_s / (self.tau_s + dt_s)
            self._previous += alpha * (measurement - self._previous)
        return self._previous

    def reset(self) -> None:
        self._previous = None
