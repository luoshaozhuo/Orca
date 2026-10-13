"""场站级控制管线：已验证参考、反馈到候选目标。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from core.application.control_laws import ControlLaw
from core.domain.control_law import ControlLawInput, ControlLawResult


class SignalProcessor(Protocol):
    """反馈预处理策略。"""

    def step(self, measurement: float, dt_s: float) -> float:
        """处理一个周期的反馈。"""
        ...

    def reset(self) -> None:
        """清除信号滤波状态。"""
        ...


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
    signal: SignalProcessor | None = None

    def evaluate(self, sample: ControlLawInput) -> ControlLawResult:
        """依次执行反馈滤波、参考轨迹、控制律。"""
        processor = self.signal
        if processor is not None:
            measured = processor.step(sample.measurement.value, sample.dt_s)
            sample = replace(
                sample,
                measurement=replace(sample.measurement, value=measured),
            )
        reference = self.trajectory.step(sample.reference.value, sample.dt_s)
        return self.law.calculate(
            replace(
                sample,
                reference=replace(sample.reference, value=reference),
            )
        )

    def reset(self) -> None:
        """功能退出时清除滤波、历史积分和参考轨迹。"""
        if self.signal is not None:
            self.signal.reset()
        self.trajectory.reset()
        self.law.reset()
