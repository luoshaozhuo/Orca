"""参考值直通和非对称斜率限制，不触及快速响应增量。"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class PassthroughTrajectory:
    """无状态参考轨迹。"""

    def step(self, reference: float, dt_s: float) -> float:
        if not math.isfinite(reference) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("invalid reference or sample period")
        return reference

    def reset(self) -> None:
        """无需清理的无状态规划器。"""


@dataclass(slots=True)
class RampTrajectory:
    """按配置的上升/下降变化率限制参考轨迹。"""

    up_per_s: float
    down_per_s: float
    _previous: float | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.up_per_s)
            or not math.isfinite(self.down_per_s)
            or self.up_per_s < 0
            or self.down_per_s < 0
        ):
            raise ValueError("ramp limits must be finite and nonnegative")

    def initialize(self, output: float) -> None:
        """从已确认的输出初始化，禁止隐式假设为零。"""
        if not math.isfinite(output):
            raise ValueError("initial trajectory output must be finite")
        self._previous = output

    def step(self, reference: float, dt_s: float) -> float:
        if not math.isfinite(reference) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("invalid reference or sample period")
        if self._previous is None:
            raise RuntimeError("ramp trajectory must be initialized")
        result = min(
            self._previous + self.up_per_s * dt_s,
            max(reference, self._previous - self.down_per_s * dt_s),
        )
        self._previous = result
        return result

    def reset(self) -> None:
        self._previous = None
