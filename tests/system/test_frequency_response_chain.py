"""内部 PFR/IR 参考到安全下发的系统级链路：真实 Redis + Mock Commander。

链路：DataPublisher → RedisSource → FrequencyResponseService（事件状态机）
→ ReferenceManager → RedisPlanningSnapshotSource → ReadOnlyPlanningCycle
→ ControlCoordinator → JointPowerAllocator → SafePlanDispatcher → Mock Commander。
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from core.application.active_power_controller import ActivePowerController
from core.application.control_coordinator import ControlCoordinator
from core.application.control_pipeline_factory import build_control_pipeline
from core.application.frequency_response import (
    FrequencyResponseService,
    IrEventMachine,
    PfrEventMachine,
    ResponsePhase,
    RocofEstimator,
)
from core.application.internal_reference import InternalReferenceService
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.application.reference_generators import InertiaParameters, PFRParameters
from core.application.reference_manager import ReferenceManager
from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.domain.config import (
    ControlPipelineConfig,
    CoordinationAlgorithm,
    CoordinationPolicyConfig,
)
from core.domain.control_authority import ControlAuthority, ControlFunction
from core.domain.control_value import ControlQuantity
from core.domain.coordination import CoordinationFunction
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

GENERATION = 7
TASK = "pfr-task"
POI = "test-poi-fr"
DISPATCH = "test-dispatch-fr"
WTG = "test-wtg-fr"
SECOND = 1_000_000_000


def iso_offset(offset_s: float) -> str:
    return datetime.fromtimestamp(time.time() + offset_s, UTC).isoformat()


def manager_with_internal(*, ir: bool) -> ReferenceManager:
    policies = {
        (ReferencePurpose.ACTIVE_BASE, "POI"): ReferenceSelection(
            ReferencePurpose.ACTIVE_BASE, "POI", ArbitrationMode.FIXED,
            ReferenceSource.GRID,
        ),
        (ReferencePurpose.PFR_DELTA, "POI"): ReferenceSelection(
            ReferencePurpose.PFR_DELTA, "POI", ArbitrationMode.FIXED,
            ReferenceSource.INTERNAL,
        ),
        (ReferencePurpose.IR_DELTA, "POI"): ReferenceSelection(
            ReferencePurpose.IR_DELTA, "POI", ArbitrationMode.FIXED,
            ReferenceSource.INTERNAL,
        ),
    }
    return ReferenceManager(policies)


def make_service(
    manager: ReferenceManager,
    authority: ControlAuthority,
    source: RedisSource,
    *,
    ir: bool,
) -> FrequencyResponseService:
    return FrequencyResponseService(
        internal=InternalReferenceService(manager, authority),
        source=source,
        device_id=POI,
        point_id="freq",
        target="POI",
        valid_for_ns=60 * SECOND,
        generation=GENERATION,
        maximum_age_ns=5 * SECOND,
        base_purpose=ReferencePurpose.ACTIVE_BASE,
        authorized_sources=frozenset({ReferenceSource.GRID, ReferenceSource.INTERNAL}),
        pfr=None if ir else PfrEventMachine(
            PFRParameters(50.0, 0.05, 10.0, 6.0, 10.0),
        ),
        ir=(
            IrEventMachine(
                InertiaParameters(50.0, 8.0, 8.0), threshold_hz_per_s=0.1,
            )
            if ir else None
        ),
        rocof=RocofEstimator(window_s=1.0, min_samples=3) if ir else None,
    )


def planning_bindings(*, ir: bool) -> dict[str, ActivePlanningBinding]:
    return {
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
                    s_max=35.0, p_auto=True, q_auto=False,
                ),
            ),
            authorized_sources=frozenset(
                {ReferenceSource.GRID, ReferenceSource.INTERNAL}
            ),
            reference_inputs=(
                PlanningReferenceBinding(
                    purpose=ReferencePurpose.ACTIVE_BASE,
                    source=ReferenceSource.GRID,
                    resource_id=DISPATCH,
                    point_id="p_ref",
                    valid_for_ns=60_000_000_000,
                ),
            ),
            active_functions=frozenset(
                {CoordinationFunction.IR if ir else CoordinationFunction.PFR}
            ),
        ),
    }


async def publish_plant(publisher: DataPublisher, p_ref: float = 6.0) -> None:
    for device, point, value in (
        (POI, "p", 5.0),
        (WTG, "p", 5.0),
        (WTG, "q", 0.0),
        (DISPATCH, "p_ref", p_ref),
    ):
        await publisher.publish(device, point, value)


async def publish_frequency(publisher: DataPublisher, value: float, offset_s: float = 0.0) -> None:
    await publisher.publish(POI, "freq", value, timestamp=iso_offset(offset_s))


async def assemble(redis_config, data_publisher: DataPublisher, *, ir: bool):
    source = RedisSource(
        redis_config.model_copy(
            update={"key_prefix": make_test_prefix(redis_config.key_prefix)}
        ),
        2.0,
    )
    await source.connect()
    manager = manager_with_internal(ir=ir)
    authority = ControlAuthority()
    authority.request_in_service(ControlFunction.AGC, operator="test")
    authority.put_in_service(ControlFunction.AGC, authorized=True)
    authority.request_in_service(ControlFunction.PFR, operator="test")
    authority.put_in_service(ControlFunction.PFR, authorized=True)
    authority.request_in_service(ControlFunction.IR, operator="test")
    authority.put_in_service(ControlFunction.IR, authorized=True)
    service = make_service(manager, authority, source, ir=ir)
    snapshot_source = RedisPlanningSnapshotSource(
        source, manager, planning_bindings(ir=ir),
        generation=GENERATION, maximum_age_ns=5_000_000_000,
        can_plan=lambda task, device, quantity: quantity == ControlQuantity.ACTIVE_POWER,
        can_contribute=lambda task, function: True,
    )
    coordination = CoordinationPolicyConfig(algorithm=CoordinationAlgorithm.SUM)
    cycle = ReadOnlyPlanningCycle(
        snapshot_source,
        {
            TASK: ActivePowerController(
                pipeline=build_control_pipeline(ControlPipelineConfig()),
                coordinator=ControlCoordinator(coordination),
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
        output_owner=lambda device, point: TASK,
    )
    return source, service, cycle, dispatcher, scenario, server, commander


async def dispatch(cycle, dispatcher, scenario) -> float:
    """执行规划与下发，返回 Mock Commander 收到的最新有功设定值。"""
    result = await cycle.execute(TASK)
    assert result.allocation.feasible
    receipt = await dispatcher.dispatch(TASK, result)
    assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
    writes = [
        write for write in scenario.writes
        if write.point_id == "active_power_setpoint"
    ]
    assert writes
    return writes[-1].value.double_value


async def test_internal_pfr_event_drives_dispatch(
    redis_config, data_publisher: DataPublisher,
) -> None:
    await publish_plant(data_publisher)
    source, service, cycle, dispatcher, scenario, server, commander = await assemble(
        redis_config, data_publisher, ir=False,
    )
    try:
        # 死区内：增量为零，仅跟踪 AGC 基准。
        await publish_frequency(data_publisher, 50.02)
        await service.update(time.time_ns())
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(6.0)
        assert service.pfr is not None and service.pfr.phase == ResponsePhase.IDLE

        # 进入死区外：+0.5 MW（0.05 Hz 有效偏差 × 10 MW/Hz）。
        await publish_frequency(data_publisher, 49.90)
        await service.update(time.time_ns())
        assert service.pfr.phase == ResponsePhase.RESPONDING
        first_event = service.pfr.event.event_id if service.pfr.event else None
        assert first_event is not None
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(6.5)

        # 扰动持续：事件标识稳定，增量不重复叠加。
        await publish_frequency(data_publisher, 49.90)
        await service.update(time.time_ns())
        assert service.pfr.event is not None
        assert service.pfr.event.event_id == first_event
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(6.5)

        # 事件期间 AGC 目标变化：基准随之生效，PFR 增量独立叠加。
        await publish_plant(data_publisher, p_ref=7.0)
        await publish_frequency(data_publisher, 49.90)
        await service.update(time.time_ns())
        assert service.pfr.event.event_id == first_event
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(7.5)

        # 频率返回死区：事件闭合，目标回到 AGC 基准。
        await publish_frequency(data_publisher, 50.01)
        await service.update(time.time_ns())
        assert service.pfr.phase != ResponsePhase.RESPONDING
        assert service.pfr.event is None
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(7.0)

        writes = [
            write.value.double_value for write in scenario.writes
            if write.point_id == "active_power_setpoint"
        ]
        assert writes == [
            pytest.approx(value) for value in (6.0, 6.5, 6.5, 7.5, 7.0)
        ]
    finally:
        await commander.close()
        await source.close()
        await server.stop(grace=0)


async def test_internal_ir_event_drives_dispatch(
    redis_config, data_publisher: DataPublisher,
) -> None:
    await publish_plant(data_publisher)
    source, service, cycle, dispatcher, scenario, server, commander = await assemble(
        redis_config, data_publisher, ir=True,
    )
    try:
        async def feed(values: list[float]) -> None:
            # 逐拍实时到达（0.25 s 间隔）：RoCoF 滑窗按真实时标估计。
            for value in values:
                await publish_frequency(data_publisher, value)
                await asyncio.sleep(0.25)
                await service.update(time.time_ns())

        # 估计窗口预热：平坦样本，RoCoF≈0，IR 发布零增量。
        await feed([50.00, 50.00, 50.00])
        assert service.ir is not None and service.ir.phase == ResponsePhase.IDLE
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(6.0)

        # 约 0.4 Hz/s 频率下降：-50×(-0.4)=+20 MW 受 IR 限幅 +8 MW 截断。
        await feed([49.90, 49.80])
        assert service.ir.phase == ResponsePhase.RESPONDING
        assert service.ir.event is not None
        first_event = service.ir.event.event_id
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(14.0)

        # 扰动持续：事件标识稳定。
        await feed([49.70])
        assert service.ir.event is not None
        assert service.ir.event.event_id == first_event

        # 频率企稳：RoCoF 回落阈值内，事件闭合并停止贡献。
        await feed([49.70, 49.70, 49.70])
        assert service.ir.phase != ResponsePhase.RESPONDING
        assert await dispatch(cycle, dispatcher, scenario) == pytest.approx(6.0)
    finally:
        await commander.close()
        await source.close()
        await server.stop(grace=0)
