"""场站有功计算与设备能力分配的纯计算组合服务。"""
from __future__ import annotations

from dataclasses import dataclass

from core.application.active_power_controller import (
    ActivePowerController,
    ActivePowerCycle,
    ActivePowerCycleResult,
)
from core.application.power_allocator import PowerAllocator
from core.domain.power_allocation import (
    DevicePowerCapability,
    PowerAllocationRequest,
    PowerAllocationResult,
)


@dataclass(frozen=True, slots=True)
class ActivePowerPlan:
    control: ActivePowerCycleResult
    allocation: PowerAllocationResult


@dataclass(slots=True)
class ActivePowerPlanner:
    """从有效输入生成设备候选设定值，不具备下发权限。"""

    controller: ActivePowerController
    allocator: PowerAllocator

    def plan(
        self,
        cycle: ActivePowerCycle,
        devices: tuple[DevicePowerCapability, ...],
    ) -> ActivePowerPlan:
        try:
            control = self.controller.evaluate(cycle)
            allocation = self.allocator.allocate(
                PowerAllocationRequest(
                    target=control.coordination.target,
                    devices=devices,
                )
            )
            if not allocation.feasible:
                raise ValueError(
                    f"device allocation infeasible: residual={allocation.residual}"
                )
            return ActivePowerPlan(control=control, allocation=allocation)
        except Exception:
            self.controller.reset()
            raise

    def reset(self) -> None:
        self.controller.reset()
