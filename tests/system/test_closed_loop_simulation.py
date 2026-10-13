"""合成设备模型闭环仿真：AGC 定值跟踪的收敛性验证入口。

设备动态为测试专用的合成一阶惯性模型，不代表任何真实设备特性；
本测试验证控制链闭环方向、稳定性与收敛性，不构成现场控制性能
验收证据（设备动态模型与性能指标属现场试验，见设计 §3.6）。

链路：合成设备模型 → DataPublisher → Redis → 规划快照 → PI 控制律
→ 协调 → 分配 → SafePlanDispatcher → Mock Commander → 写回合成模型。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.application.active_power_controller import ActivePowerController
from core.application.control_coordinator import ControlCoordinator
from core.application.control_pipeline_factory import build_control_pipeline
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.application.reference_manager import ReferenceManager
from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.domain.config import (
    ControlLawStageConfig,
    ControlPipelineConfig,
    CoordinationPolicyConfig,
)
from core.domain.control_value import ControlQuantity
from core.domain.device_capability import DeviceTechnology
from core.domain.reference import (
    ArbitrationMode,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
)
from core.domain.safe_dispatch import DispatchStatus, OutputBinding
from infrastructure.redis.planning_snapshot import (
    ActivePlanningBinding,
    DevicePlanningBinding,
    PlanningReferenceBinding,
    RedisPlanningSnapshotSource,
)
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
from tests.support.commander import CommanderPoint, CommanderScenario, start_commander
from tests.support.redis_data import DataPublisher
from tests.support.redis_data import test_prefix as make_test_prefix

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.system,
    pytest.mark.real_service,
    pytest.mark.mock_service,
]

GENERATION = 3
TASK = "sim"
POI = "test-poi-sim"
DISPATCH = "test-dispatch-sim"
WTG = "test-wtg-sim"


@dataclass(slots=True)
class SyntheticFirstOrderDevice:
    """合成一阶惯性设备：p(k+1) = p(k) + (setpoint - p(k)) * dt/tau。"""

    tau_s: float = 0.5
    p: float = 5.0

    def apply(self, setpoint: float, dt_s: float) -> None:
        self.p += (setpoint - self.p) * min(dt_s / self.tau_s, 1.0)


async def test_agc_closed_loop_converges_after_reference_step(
    redis_config, data_publisher: DataPublisher,
) -> None:
    device = SyntheticFirstOrderDevice()
    source = RedisSource(
        redis_config.model_copy(
            update={"key_prefix": make_test_prefix(redis_config.key_prefix)}
        ),
        2.0,
    )
    await source.connect()
    manager = ReferenceManager({
        (ReferencePurpose.ACTIVE_BASE, "POI"): ReferenceSelection(
            ReferencePurpose.ACTIVE_BASE, "POI", ArbitrationMode.FIXED,
            ReferenceSource.GRID,
        ),
    })
    snapshot_source = RedisPlanningSnapshotSource(
        source, manager, {
            TASK: ActivePlanningBinding(
                target="POI",
                poi_device_id=POI,
                poi_active_point="p",
                minimum=0.0,
                maximum=30.0,
                dt_s=1.0,
                devices=(
                    DevicePlanningBinding(
                        device_id=WTG, p_point="p", q_point="q",
                        technology=DeviceTechnology.WIND,
                        p_min=0.0, p_max=30.0, q_min=-5.0, q_max=5.0,
                        s_max=35.0, p_auto=True,
                    ),
                ),
                authorized_sources=frozenset({ReferenceSource.GRID}),
                reference_inputs=(
                    PlanningReferenceBinding(
                        purpose=ReferencePurpose.ACTIVE_BASE,
                        source=ReferenceSource.GRID,
                        resource_id=DISPATCH,
                        point_id="p_ref",
                        valid_for_ns=60_000_000_000,
                    ),
                ),
            ),
        },
        generation=GENERATION, maximum_age_ns=5_000_000_000,
        can_plan=lambda task, dev, quantity: True,
    )
    pipeline = build_control_pipeline(
        ControlPipelineConfig(law=ControlLawStageConfig(
            algorithm="pi", kp=0.3, ki=0.1, kaw=0.0,
        )),
    )
    cycle = ReadOnlyPlanningCycle(
        snapshot_source,
        {
            TASK: ActivePowerController(
                pipeline=pipeline,
                coordinator=ControlCoordinator(CoordinationPolicyConfig()),
            ),
        },
        JointPowerAllocator(),
        maximum_age_ns=5_000_000_000,
    )
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={
            (WTG, "p"): CommanderPoint(writable=False),
            (WTG, "active_power_setpoint"): CommanderPoint(),
        },
    )
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    dispatcher = SafePlanDispatcher(
        commander,
        (
            OutputBinding(
                device_id=WTG, quantity=ControlQuantity.ACTIVE_POWER,
                point_id="active_power_setpoint", minimum=0.0, maximum=30.0,
            ),
        ),
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: True,
        generation=lambda: GENERATION,
        output_owner=lambda dev, point: TASK,
    )

    async def publish_state(p_ref: float) -> None:
        await data_publisher.publish(POI, "p", device.p)
        await data_publisher.publish(WTG, "p", device.p)
        await data_publisher.publish(WTG, "q", 0.0)
        await data_publisher.publish(DISPATCH, "p_ref", p_ref)

    try:
        history: list[float] = []
        reference = 5.0
        for step in range(48):
            # 第 6 拍参考阶跃 5 → 8 MW。
            if step == 6:
                reference = 8.0
            await publish_state(reference)
            result = await cycle.execute(TASK)
            assert result.allocation.feasible
            receipt = await dispatcher.dispatch(TASK, result)
            assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
            setpoint = scenario.writes[-1].value.double_value
            device.apply(setpoint, 1.0)
            history.append(device.p)
        final = history[-1]
        # 闭环方向正确、无发散、阶跃后收敛到参考附近。
        assert final == pytest.approx(8.0, abs=0.3)
        assert max(history[6:]) < 10.0
        assert all(value >= 0.0 for value in history)
        # 收敛趋势：末段偏差小于阶跃后首拍偏差。
        assert abs(history[-1] - 8.0) < abs(history[6] - 8.0)
    finally:
        await commander.close()
        await source.close()
        await server.stop(grace=0)
