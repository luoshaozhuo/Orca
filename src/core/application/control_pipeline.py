"""场站级控制管线：已验证参考、反馈到候选目标。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from core.application.control_laws import ControlLaw
from core.domain.control_law import ControlLawInput, ControlLawResult


class TrajectoryPlanner(Protocol):
    """独立参考轨迹策略。"""

    def step(self, reference: float, dt_s: float) -> float:
        """计算一个周期的参考轨迹。"""
        ...

    def reset(self) -> None:
        """重置周期状态。"""
        ...


@dataclass(slots=True)
class ControlPipeline:
    """单控制功能管线，不进行控制权仲裁和设备分配。"""

    trajectory: TrajectoryPlanner
    law: ControlLaw

    def evaluate(self, sample: ControlLawInput) -> ControlLawResult:
        """用轨迹后的参考值执行控制律。"""
        reference = self.trajectory.step(
            sample.reference.value,
            sample.dt_s,
        )
        value = replace(sample.reference, value=reference)
        return self.law.calculate(replace(sample, reference=value))

    def reset(self) -> None:
        """功能退出时清除历史积分和参考轨迹。"""
        self.trajectory.reset()
        self.law.reset()
