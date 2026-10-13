"""周期内执行只读有功控制计算和设备联合分配。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from core.application.active_power_controller import ActivePowerController
from core.application.control_trajectory import RampTrajectory
from core.application.joint_power_allocator import JointAllocationResult, JointPowerAllocator
from core.application.port.planning_snapshot import PlanningSnapshotPort


@dataclass(frozen=True, slots=True)
class PlanningCycleResult:
    """安全待执行的候选计划；无 Commander 指令。"""

    allocation: JointAllocationResult
    acquired_at_ns: int


class PlanningCyclePort(Protocol):
    async def execute(self, task_id: str) -> PlanningCycleResult:
        """运行一次计算周期。"""
        ...

    def reset(self, task_id: str) -> None:
        """丢弃任务历史控制状态。"""
        ...


class ReadOnlyPlanningCycle:
    """输入快照、控制律、协调、联合设备分配全部在内存中完成。"""

    def __init__(
        self,
        snapshots: PlanningSnapshotPort,
        controllers: dict[str, ActivePowerController],
        allocator: JointPowerAllocator,
        *,
        maximum_age_ns: int,
    ) -> None:
        if maximum_age_ns <= 0:
            raise ValueError("planning maximum age must be positive")
        self._snapshots = snapshots
        self._controllers = controllers
        self._allocator = allocator
        self._maximum_age_ns = maximum_age_ns

    async def execute(self, task_id: str) -> PlanningCycleResult:
        if task_id not in self._controllers:
            raise ValueError(f"no planning controller for {task_id}")
        controller = self._controllers[task_id]
        try:
            snapshot = await self._snapshots.acquire(task_id)
            started_at_ns = time.time_ns()
            if (
                snapshot.acquired_at_ns > started_at_ns
                or started_at_ns - snapshot.acquired_at_ns > self._maximum_age_ns
            ):
                raise ValueError("planning observation snapshot is stale")
            # 轨迹只能用已通过快照门禁的实际功率初始化，不假定零输出。
            ramp = controller.pipeline.trajectory
            if isinstance(ramp, RampTrajectory) and not ramp.initialized:
                snapshot.active.law_input()
                ramp.initialize(snapshot.active.measurement.value)
            control = controller.evaluate(snapshot.active)
            allocation = self._allocator.allocate(
                snapshot.devices,
                p_target=control.coordination.target.value,
                q_target=None,
                now_ns=started_at_ns,
                maximum_age_ns=self._maximum_age_ns,
            )
            if not allocation.feasible:
                raise ValueError(
                    f"planning allocation blocked: {allocation.reason}"
                )
            # 计算结束时再次检查：慢计算不能产生过期控制计划。
            completed_ns = time.time_ns()
            snapshot.devices.validate(
                now_ns=completed_ns,
                maximum_age_ns=self._maximum_age_ns,
            )
            if completed_ns - snapshot.acquired_at_ns > self._maximum_age_ns:
                raise ValueError("planning snapshot expired during calculation")
            return PlanningCycleResult(
                allocation=allocation,
                acquired_at_ns=snapshot.acquired_at_ns,
            )
        except BaseException:
            controller.reset()
            raise

    def reset(self, task_id: str) -> None:
        controller = self._controllers.get(task_id)
        if controller is not None:
            controller.reset()
