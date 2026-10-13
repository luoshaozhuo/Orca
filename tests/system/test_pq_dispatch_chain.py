"""P/Q 联合规划到安全下发的系统级链路：真实 Redis + Mock Commander。

链路：DataPublisher（模拟 wind-hub Sink）→ RedisSource →
RedisPlanningSnapshotSource → ReadOnlyPlanningCycle → JointPowerAllocator
→ SafePlanDispatcher → CommanderClient → Mock Commander gRPC Server。
控制指令只到达 Mock Commander，绝不接触现场设备。
"""
from __future__ import annotations

import pytest

from core.application.active_power_controller import ActivePowerController
from core.application.control_coordinator import ControlCoordinator
from core.application.control_pipeline_factory import (
    build_control_pipeline,
    build_reactive_controller,
)
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.application.reference_manager import ReferenceManager
from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.domain.avc import AvcMode
from core.domain.config import ControlPipelineConfig, CoordinationPolicyConfig
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
    ReactivePlanningBinding,
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

GENERATION = 7
TASK = "pq"
POI = "test-poi"
DISPATCH = "test-dispatch"
WTG = "test-wtg001"


def reference_manager() -> ReferenceManager:
    policies = {}
    for purpose in (ReferencePurpose.ACTIVE_BASE, ReferencePurpose.REACTIVE_BASE):
        policies[(purpose, "POI")] = ReferenceSelection(
            purpose, "POI", ArbitrationMode.FIXED, ReferenceSource.GRID,
        )
    return ReferenceManager(policies)


def planning_bindings() -> dict[str, ActivePlanningBinding]:
    reactive = ReactivePlanningBinding(
        target="POI",
        mode=AvcMode.REACTIVE_POWER,
        poi_device_id=POI,
        poi_reactive_point="q",
        minimum=-5.0,
        maximum=5.0,
        authorized_sources=frozenset({ReferenceSource.GRID}),
        reference_inputs=(
            PlanningReferenceBinding(
                purpose=ReferencePurpose.REACTIVE_BASE,
                source=ReferenceSource.GRID,
                resource_id=DISPATCH,
                point_id="q_ref",
                valid_for_ns=60_000_000_000,
            ),
        ),
    )
    return {
        TASK: ActivePlanningBinding(
            target="POI",
            poi_device_id=POI,
            poi_active_point="p",
            minimum=0.0,
            maximum=10.0,
            dt_s=1.0,
            devices=(
                DevicePlanningBinding(
                    device_id=WTG, p_point="p", q_point="q",
                    technology=DeviceTechnology.WIND,
                    p_min=0.0, p_max=10.0, q_min=-5.0, q_max=5.0,
                    s_max=11.0, p_auto=True, q_auto=True,
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
            reactive=reactive,
        ),
    }


async def publish_plant_data(publisher: DataPublisher) -> None:
    for device, point, value in (
        (POI, "p", 5.0),
        (POI, "q", 0.5),
        (WTG, "p", 5.0),
        (WTG, "q", 0.0),
        (DISPATCH, "p_ref", 6.0),
        (DISPATCH, "q_ref", 2.0),
    ):
        await publisher.publish(device, point, value)


def commander_scenario() -> CommanderScenario:
    return CommanderScenario(
        enabled_devices={WTG},
        points={
            (WTG, "p"): CommanderPoint(writable=False),
            (WTG, "q"): CommanderPoint(writable=False),
            (WTG, "active_power_setpoint"): CommanderPoint(),
            (WTG, "reactive_power_setpoint"): CommanderPoint(),
        },
    )


def output_bindings() -> tuple[OutputBinding, ...]:
    return (
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
        ),
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.REACTIVE_POWER,
            point_id="reactive_power_setpoint", minimum=-5.0, maximum=5.0,
        ),
    )


async def test_pq_planning_dispatch_reaches_mock_commander(
    redis_config, data_publisher: DataPublisher,
) -> None:
    await publish_plant_data(data_publisher)
    source = RedisSource(
        redis_config.model_copy(
            update={"key_prefix": make_test_prefix(redis_config.key_prefix)}
        ),
        2.0,
    )
    await source.connect()
    snapshot_source = RedisPlanningSnapshotSource(
        source, reference_manager(), planning_bindings(),
        generation=GENERATION, maximum_age_ns=5_000_000_000,
        can_plan=lambda task, device, quantity: True,
    )
    pipeline = build_control_pipeline(ControlPipelineConfig())
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
        reactive_controllers={
            TASK: build_reactive_controller(
                AvcMode.REACTIVE_POWER, ControlPipelineConfig()
            ),
        },
    )
    scenario = commander_scenario()
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    try:
        dispatcher = SafePlanDispatcher(
            commander,
            output_bindings(),
            maximum_age_ns=5_000_000_000,
            permitted=lambda task_id: True,
            generation=lambda: GENERATION,
            output_owner=lambda device, point: TASK,
        )
        result = await cycle.execute(TASK)
        assert result.allocation.feasible
        receipt = await dispatcher.dispatch(TASK, result)
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        writes = {
            (write.device_id, write.point_id): write.value.double_value
            for write in scenario.writes
        }
        assert writes == {
            (WTG, "active_power_setpoint"): pytest.approx(6.0),
            (WTG, "reactive_power_setpoint"): pytest.approx(2.0),
        }
    finally:
        await commander.close()
        await source.close()
        await server.stop(grace=0)
