"""场站有功控制计算编排：参考选择、控制律、功能协调。"""
from __future__ import annotations

from dataclasses import dataclass

from core.application.control_coordinator import ControlCoordinator
from core.application.control_pipeline import ControlPipeline
from core.domain.control_law import ControlLawInput, ControlLawResult
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
)
from core.domain.coordination import (
    ControlDemand,
    CoordinationContext,
    CoordinationResult,
)


@dataclass(frozen=True, slots=True)
class ActivePowerCycle:
    """同一周期的已授权有效输入快照；不访问外部服务。"""

    base: ControlValue
    measurement: ControlValue
    dt_s: float
    minimum: float
    maximum: float
    demands: tuple[ControlDemand, ...] = ()
    feedforward: float = 0.0
    achievable: float | None = None
    base_generation: int = 0

    def law_input(self) -> ControlLawInput:
        return ControlLawInput(
            reference=self.base,
            measurement=self.measurement,
            dt_s=self.dt_s,
            minimum=self.minimum,
            maximum=self.maximum,
            feedforward=self.feedforward,
            achievable=self.achievable,
        )


@dataclass(frozen=True, slots=True)
class ActivePowerCycleResult:
    """候选控制值及协调结果；不代表任何设备写入授权。"""

    control: ControlLawResult
    coordination: CoordinationResult


@dataclass(slots=True)
class ActivePowerController:
    """AGC 基准和 PFR/IR 增量的独立数学编排。"""

    pipeline: ControlPipeline
    coordinator: ControlCoordinator

    def evaluate(self, cycle: ActivePowerCycle) -> ActivePowerCycleResult:
        """只接收同一口径的控制量，异常时废弃有状态算法历史。"""
        try:
            input_value = cycle.law_input()
            if (
                cycle.base.quantity != ControlQuantity.ACTIVE_POWER
                or cycle.base.scope not in (ControlScope.POI, ControlScope.PLANT)
            ):
                raise ValueError("active-power controller requires plant or POI power")
            control = self.pipeline.evaluate(input_value)
            base = control.candidate
            context = CoordinationContext(
                base=base,
                demands=cycle.demands,
                base_generation=cycle.base_generation,
            )
            coordinated = self.coordinator.coordinate(context)
            if not cycle.minimum <= coordinated.target.value <= cycle.maximum:
                raise ValueError("coordinated target exceeds cycle limits")
            return ActivePowerCycleResult(control=control, coordination=coordinated)
        except Exception:
            self.pipeline.reset()
            raise

    def reset(self) -> None:
        """退出自动功能时清除算法状态。"""
        self.pipeline.reset()
