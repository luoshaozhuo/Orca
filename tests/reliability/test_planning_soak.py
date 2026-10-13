"""短时浸泡冒烟：规划周期反复执行结果确定、无状态漂移。

长时间浸泡（小时级）按任务书要求不伪造为已执行；本测试只验证
连续执行的确定性与积分状态有界，长时间运行需专用环境另行触发。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from core.application.active_power_controller import (
    ActivePowerController,
    ActivePowerCycle,
)
from core.application.control_coordinator import ControlCoordinator
from core.application.control_laws import PIControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_trajectory import PassthroughTrajectory
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.port.planning_snapshot import PlanningSnapshot
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.domain.capability_snapshot import (
    DeviceCapabilityRecord,
    DeviceCapabilitySnapshot,
)
from core.domain.config import CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.device_capability import DevicePQCapability, DeviceTechnology

pytestmark = [pytest.mark.asyncio, pytest.mark.reliability, pytest.mark.soak]

ITERATIONS = 200


def value(amount: float, role: ControlValueRole) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI", role,
        ControlValueKind.ABSOLUTE, amount, "MW",
    )


@dataclass
class StableSnapshotSource:
    async def acquire(self, task_id: str) -> PlanningSnapshot:
        now = time.time_ns()
        device = DevicePQCapability(
            device_id="wtg", technology=DeviceTechnology.WIND,
            p_current=5.0, q_current=0.0,
            p_min=0.0, p_max=10.0, q_min=-5.0, q_max=5.0, s_max=10.0,
        )
        record = DeviceCapabilityRecord(
            capability=device, timestamp_ns=now,
            generation=1, quality_good=True,
            p_authorized=True, q_authorized=False,
        )
        return PlanningSnapshot(
            active=ActivePowerCycle(
                base=value(6.0, ControlValueRole.REFERENCE),
                measurement=value(5.0, ControlValueRole.MEASUREMENT),
                dt_s=0.1, minimum=0.0, maximum=10.0,
            ),
            devices=DeviceCapabilitySnapshot(
                records=(record,), generation=1,
                sampled_at_ns=now, valid_until_ns=now + 5_000_000_000,
            ),
            acquired_at_ns=now,
        )


async def test_repeated_cycles_are_deterministic_and_bounded() -> None:
    controller = ActivePowerController(
        pipeline=ControlPipeline(
            trajectory=PassthroughTrajectory(),
            law=PIControlLaw(kp=0.5, ki=1.0),
        ),
        coordinator=ControlCoordinator(CoordinationPolicyConfig()),
    )
    cycle = ReadOnlyPlanningCycle(
        StableSnapshotSource(), {"active": controller}, JointPowerAllocator(),
        maximum_age_ns=5_000_000_000,
    )
    started = time.monotonic()
    targets: list[float] = []
    for _ in range(ITERATIONS):
        result = await cycle.execute("active")
        assert result.allocation.feasible
        targets.append(result.allocation.active[0].value)
    elapsed = time.monotonic() - started
    # 固定输入下 PI 输出收敛：末段结果恒定，积分不漂移。
    assert len(set(targets[-20:])) == 1
    assert all(0.0 <= target <= 10.0 for target in targets)
    # 200 个纯计算周期应在秒级完成；超时意味着状态或等待泄漏。
    assert elapsed < 10.0
