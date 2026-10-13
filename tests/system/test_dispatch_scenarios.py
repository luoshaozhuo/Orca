"""规划-下发链路的运行场景矩阵：真实 Redis + Mock Commander。

覆盖：AGC 限值模式、AVC 定电压/定功率因数模式、储能 SOC 边界、
控制权撤销（人工接管）、不可行目标、设备退出自动、参考来源切换。
所有场景共用「模拟发布 → 真实 Redis → 规划 → 分配 → 安全下发 →
Mock Commander」链路；控制指令绝不接触现场设备。
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
from core.domain.agc import AgcMode
from core.domain.avc import AvcMode
from core.domain.config import (
    ControlLawStageConfig,
    ControlPipelineConfig,
    CoordinationPolicyConfig,
)
from core.domain.control_value import ControlQuantity, ControlValueKind
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
    StoragePlanningBinding,
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
POI = "test-poi-sc"
DISPATCH = "test-dispatch-sc"
WTG = "test-wtg-sc"
ESS = "test-ess-sc"


def wind_device(**overrides: object) -> DevicePlanningBinding:
    values = {
        "device_id": WTG, "p_point": "p", "q_point": "q",
        "technology": DeviceTechnology.WIND,
        "p_min": 0.0, "p_max": 10.0, "q_min": -5.0, "q_max": 5.0,
        "s_max": 11.0, "p_auto": True, "q_auto": True,
    }
    values.update(overrides)
    return DevicePlanningBinding(**values)  # type: ignore[arg-type]


def active_binding(
    reference_inputs: tuple[PlanningReferenceBinding, ...],
    *,
    devices: tuple[DevicePlanningBinding, ...] = (wind_device(),),
    reactive: ReactivePlanningBinding | None = None,
    mode: AgcMode = AgcMode.SETPOINT,
    poi_available_point: str | None = None,
) -> ActivePlanningBinding:
    return ActivePlanningBinding(
        target="POI",
        mode=mode,
        poi_device_id=POI,
        poi_active_point="p",
        poi_available_point=poi_available_point,
        minimum=0.0,
        maximum=10.0,
        dt_s=1.0,
        devices=devices,
        authorized_sources=frozenset(
            {ReferenceSource.GRID, ReferenceSource.MANUAL}
        ),
        reference_inputs=reference_inputs,
        reactive=reactive,
    )


def p_ref_input(point: str = "p_ref", source: ReferenceSource = ReferenceSource.GRID) -> PlanningReferenceBinding:
    return PlanningReferenceBinding(
        purpose=ReferencePurpose.ACTIVE_BASE,
        source=source,
        resource_id=DISPATCH,
        point_id=point,
        valid_for_ns=60_000_000_000,
    )


class Chain:
    def __init__(self, source, manager, cycle, dispatcher, scenario, server, commander):
        self.source = source
        self.manager = manager
        self.cycle = cycle
        self.dispatcher = dispatcher
        self.scenario = scenario
        self.server = server
        self.commander = commander

    async def close(self) -> None:
        await self.commander.close()
        await self.source.close()
        await self.server.stop(grace=0)

    async def dispatch(self) -> tuple[object, dict[tuple[str, str], float]]:
        """执行一次规划与下发，返回 (回执, {(设备, 测点): 值}) 增量写入。"""
        before = len(self.scenario.writes)
        result = await self.cycle.execute(TASK)
        receipt = await self.dispatcher.dispatch(TASK, result)
        writes = {
            (write.device_id, write.point_id): write.value.double_value
            for write in self.scenario.writes[before:]
        }
        return receipt, writes


async def start_chain(
    redis_config,
    *,
    binding: ActivePlanningBinding,
    manager: ReferenceManager,
    pipeline: ControlPipelineConfig | None = None,
    reactive_mode: AvcMode | None = None,
    permitted: bool = True,
    devices: set[str] | None = None,
) -> Chain:
    source = RedisSource(
        redis_config.model_copy(
            update={"key_prefix": make_test_prefix(redis_config.key_prefix)}
        ),
        2.0,
    )
    await source.connect()
    snapshot_source = RedisPlanningSnapshotSource(
        source, manager, {TASK: binding},
        generation=GENERATION, maximum_age_ns=5_000_000_000,
        can_plan=lambda task, device, quantity: True,
    )
    reactive_controllers = None
    if reactive_mode is not None:
        reactive_controllers = {
            TASK: build_reactive_controller(
                reactive_mode, pipeline or ControlPipelineConfig()
            ),
        }
    cycle = ReadOnlyPlanningCycle(
        snapshot_source,
        {
            TASK: ActivePowerController(
                pipeline=build_control_pipeline(pipeline or ControlPipelineConfig()),
                coordinator=ControlCoordinator(CoordinationPolicyConfig()),
            ),
        },
        JointPowerAllocator(),
        maximum_age_ns=5_000_000_000,
        reactive_controllers=reactive_controllers,
    )
    enabled = devices or {WTG}
    points = {
        (device, point): CommanderPoint(writable=False)
        for device in enabled
        for point in ("p", "q")
    }
    points.update({
        (device, "active_power_setpoint"): CommanderPoint()
        for device in enabled
    })
    points.update({
        (device, "reactive_power_setpoint"): CommanderPoint()
        for device in enabled
    })
    scenario = CommanderScenario(enabled_devices=enabled, points=points)
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    bindings = tuple(
        OutputBinding(
            device_id=device, quantity=quantity,
            point_id=point, minimum=minimum, maximum=maximum,
        )
        for device in enabled
        for quantity, point, minimum, maximum in (
            (ControlQuantity.ACTIVE_POWER, "active_power_setpoint", -30.0, 30.0),
            (ControlQuantity.REACTIVE_POWER, "reactive_power_setpoint", -5.0, 5.0),
        )
    )
    dispatcher = SafePlanDispatcher(
        commander,
        bindings,
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: permitted,
        generation=lambda: GENERATION,
        output_owner=lambda device, point: TASK,
    )
    return Chain(source, manager, cycle, dispatcher, scenario, server, commander)


async def publish_wind_plant(publisher: DataPublisher, p_ref: float = 6.0) -> None:
    for device, point, value in (
        (POI, "p", 5.0),
        (POI, "q", 0.5),
        (WTG, "p", 5.0),
        (WTG, "q", 0.0),
        (DISPATCH, "p_ref", p_ref),
    ):
        await publisher.publish(device, point, value)


def fixed_manager(*purposes: ReferencePurpose) -> ReferenceManager:
    return ReferenceManager({
        (purpose, "POI"): ReferenceSelection(
            purpose, "POI", ArbitrationMode.FIXED, ReferenceSource.GRID,
        )
        for purpose in purposes
    })


async def test_agc_limit_mode_releases_available_under_limit(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """限值模式：目标 = min(可用出力, 上限)，限值只是约束而非跟踪定值。"""
    chain = await start_chain(
        redis_config,
        binding=active_binding(
            (
                PlanningReferenceBinding(
                    purpose=ReferencePurpose.ACTIVE_LIMIT,
                    source=ReferenceSource.GRID,
                    resource_id=DISPATCH,
                    point_id="p_limit",
                    valid_for_ns=60_000_000_000,
                    kind=ControlValueKind.UPPER_LIMIT,
                ),
            ),
            mode=AgcMode.LIMIT,
            poi_available_point="p_avail",
        ),
        manager=fixed_manager(ReferencePurpose.ACTIVE_LIMIT),
        pipeline=ControlPipelineConfig(
            law=ControlLawStageConfig(algorithm="active_limit")
        ),
    )
    try:
        for device, point, value in (
            (POI, "p", 5.0), (WTG, "p", 5.0), (WTG, "q", 0.0),
            (POI, "p_avail", 8.0), (DISPATCH, "p_limit", 6.5),
        ):
            await data_publisher.publish(device, point, value)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(WTG, "active_power_setpoint")] == pytest.approx(6.5)

        # 限值高于可用出力：释放可用出力 8.0，而非跟踪限值 9.0。
        await data_publisher.publish(DISPATCH, "p_limit", 9.0)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(WTG, "active_power_setpoint")] == pytest.approx(8.0)
    finally:
        await chain.close()


async def test_avc_voltage_mode_drives_reactive_dispatch(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """定电压模式：电压低于参考时注入正无功（kp=0.5 Mvar/kV）。"""
    reactive = ReactivePlanningBinding(
        target="POI",
        mode=AvcMode.VOLTAGE,
        poi_device_id=POI,
        poi_reactive_point="q",
        poi_voltage_point="u",
        minimum=-5.0,
        maximum=5.0,
        authorized_sources=frozenset({ReferenceSource.GRID}),
        reference_inputs=(
            PlanningReferenceBinding(
                purpose=ReferencePurpose.VOLTAGE_BASE,
                source=ReferenceSource.GRID,
                resource_id=DISPATCH,
                point_id="u_ref",
                valid_for_ns=60_000_000_000,
            ),
        ),
    )
    chain = await start_chain(
        redis_config,
        binding=active_binding((p_ref_input(),), reactive=reactive),
        manager=fixed_manager(
            ReferencePurpose.ACTIVE_BASE, ReferencePurpose.VOLTAGE_BASE
        ),
        pipeline=ControlPipelineConfig(
            law=ControlLawStageConfig(algorithm="p", kp=0.5)
        ),
        reactive_mode=AvcMode.VOLTAGE,
    )
    try:
        await publish_wind_plant(data_publisher)
        await data_publisher.publish(POI, "u", 33.0)
        await data_publisher.publish(DISPATCH, "u_ref", 35.0)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        # 实测 0.5 + 0.5 × (35 − 33) = 1.5 Mvar（电压律在实测无功上叠加修正）。
        assert writes[(WTG, "reactive_power_setpoint")] == pytest.approx(1.5)
    finally:
        await chain.close()


async def test_avc_power_factor_mode_drives_reactive_dispatch(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """定功率因数模式：q_ref = p × tan(acos(pf))，滞后为正。"""
    reactive = ReactivePlanningBinding(
        target="POI",
        mode=AvcMode.POWER_FACTOR,
        poi_device_id=POI,
        poi_reactive_point="q",
        minimum=-5.0,
        maximum=5.0,
        authorized_sources=frozenset({ReferenceSource.GRID}),
        reference_inputs=(
            PlanningReferenceBinding(
                purpose=ReferencePurpose.POWER_FACTOR,
                source=ReferenceSource.GRID,
                resource_id=DISPATCH,
                point_id="pf_ref",
                valid_for_ns=60_000_000_000,
            ),
        ),
    )
    chain = await start_chain(
        redis_config,
        binding=active_binding((p_ref_input(),), reactive=reactive),
        manager=fixed_manager(
            ReferencePurpose.ACTIVE_BASE, ReferencePurpose.POWER_FACTOR
        ),
        reactive_mode=AvcMode.POWER_FACTOR,
    )
    try:
        for device, point, value in (
            (POI, "p", 4.0), (POI, "q", 0.0),
            (WTG, "p", 4.0), (WTG, "q", 0.0),
            (DISPATCH, "p_ref", 4.0), (DISPATCH, "pf_ref", 0.9),
        ):
            await data_publisher.publish(device, point, value)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        # 4.0 × tan(acos(0.9)) ≈ 1.937 Mvar。
        assert writes[(WTG, "reactive_power_setpoint")] == pytest.approx(
            4.0 * 0.4843221, abs=1e-3
        )
    finally:
        await chain.close()


def storage_binding(soc_min: float = 0.1, soc_max: float = 0.9) -> ActivePlanningBinding:
    return active_binding(
        (p_ref_input(),),
        devices=(
            DevicePlanningBinding(
                device_id=ESS, p_point="p", q_point="q",
                technology=DeviceTechnology.STORAGE,
                p_min=-10.0, p_max=10.0, q_min=-5.0, q_max=5.0,
                s_max=11.0, p_auto=True, q_auto=False,
                storage=StoragePlanningBinding(
                    soc_point="soc",
                    soc_min=soc_min,
                    soc_max=soc_max,
                    energy_mwh=0.001,
                    charge_efficiency=1.0,
                    discharge_efficiency=1.0,
                ),
            ),
        ),
    )


async def publish_storage_plant(
    publisher: DataPublisher, soc: float, p_ref: float
) -> None:
    for device, point, value in (
        (POI, "p", 1.0), (ESS, "p", 1.0), (ESS, "q", 0.0),
        (ESS, "soc", soc), (DISPATCH, "p_ref", p_ref),
    ):
        await publisher.publish(device, point, value)


async def test_storage_soc_outside_window_fails_closed(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """SOC 越出运行窗口：规划失败关闭，绝不下发任何写入。"""
    chain = await start_chain(
        redis_config,
        binding=storage_binding(),
        manager=fixed_manager(ReferencePurpose.ACTIVE_BASE),
        devices={ESS},
    )
    try:
        await publish_storage_plant(data_publisher, soc=0.95, p_ref=2.0)
        with pytest.raises(ValueError, match="SOC"):
            await chain.cycle.execute(TASK)
        assert chain.scenario.writes == []
    finally:
        await chain.close()


async def test_storage_soc_headroom_limits_dispatch(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """SOC 接近上限：可放功率受能量约束，超界目标判不可行且不下发。"""
    chain = await start_chain(
        redis_config,
        binding=storage_binding(),
        manager=fixed_manager(ReferencePurpose.ACTIVE_BASE),
        devices={ESS},
    )
    try:
        # dt=1s、0.001 MWh、SOC 0.9：可放上限 = 0.8×0.001×3600 = 2.88 MW。
        await publish_storage_plant(data_publisher, soc=0.9, p_ref=5.0)
        with pytest.raises(ValueError, match="allocation blocked"):
            await chain.cycle.execute(TASK)
        assert chain.scenario.writes == []

        # 能量约束内的目标正常下发。
        await publish_storage_plant(data_publisher, soc=0.9, p_ref=2.0)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(ESS, "active_power_setpoint")] == pytest.approx(2.0)
    finally:
        await chain.close()


async def test_revoked_authority_blocks_all_writes(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """控制权撤销（人工接管/旁路）：计划再新也不提交任何写入。"""
    chain = await start_chain(
        redis_config,
        binding=active_binding((p_ref_input(),)),
        manager=fixed_manager(ReferencePurpose.ACTIVE_BASE),
        permitted=False,
    )
    try:
        await publish_wind_plant(data_publisher)
        result = await chain.cycle.execute(TASK)
        assert result.allocation.feasible
        receipt = await chain.dispatcher.dispatch(TASK, result)
        assert receipt.status == DispatchStatus.BLOCKED
        assert chain.scenario.writes == []
    finally:
        await chain.close()


async def test_target_beyond_capability_is_saturated_not_overshot(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """不可行目标：候选被限幅到场上限 10 MW，绝不按 50 MW 超发。"""
    chain = await start_chain(
        redis_config,
        binding=active_binding((p_ref_input(),)),
        manager=fixed_manager(ReferencePurpose.ACTIVE_BASE),
    )
    try:
        await publish_wind_plant(data_publisher, p_ref=50.0)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(WTG, "active_power_setpoint")] == pytest.approx(10.0)
    finally:
        await chain.close()


async def test_device_out_of_auto_fails_closed(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """设备退出自动（能力变化）：无可分配能力时规划失败关闭、零写入。"""
    chain = await start_chain(
        redis_config,
        binding=active_binding(
            (p_ref_input(),),
            devices=(wind_device(p_auto=False, q_auto=False),),
        ),
        manager=fixed_manager(ReferencePurpose.ACTIVE_BASE),
    )
    try:
        await publish_wind_plant(data_publisher)
        with pytest.raises(ValueError, match="capabilit"):
            await chain.cycle.execute(TASK)
        assert chain.scenario.writes == []
    finally:
        await chain.close()


async def test_authorized_source_switch_takes_effect(
    redis_config, data_publisher: DataPublisher,
) -> None:
    """主备仲裁：授权切换后备用来源生效；未授权与未配置来源被拒绝。"""
    manager = ReferenceManager({
        (ReferencePurpose.ACTIVE_BASE, "POI"): ReferenceSelection(
            ReferencePurpose.ACTIVE_BASE, "POI",
            ArbitrationMode.PRIMARY_BACKUP, ReferenceSource.GRID,
            backups=(ReferenceSource.MANUAL,),
        ),
    })
    chain = await start_chain(
        redis_config,
        binding=active_binding(
            (
                p_ref_input("p_ref", ReferenceSource.GRID),
                p_ref_input("p_ref_manual", ReferenceSource.MANUAL),
            ),
        ),
        manager=manager,
    )
    try:
        await publish_wind_plant(data_publisher, p_ref=6.0)
        await data_publisher.publish(DISPATCH, "p_ref_manual", 7.0)
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(WTG, "active_power_setpoint")] == pytest.approx(6.0)

        with pytest.raises(PermissionError):
            manager.select_source(
                ReferencePurpose.ACTIVE_BASE, "POI",
                ReferenceSource.MANUAL, authorized=False,
            )
        with pytest.raises(ValueError, match="not configured"):
            manager.select_source(
                ReferencePurpose.ACTIVE_BASE, "POI",
                ReferenceSource.THIRD_PARTY, authorized=True,
            )

        manager.select_source(
            ReferencePurpose.ACTIVE_BASE, "POI",
            ReferenceSource.MANUAL, authorized=True,
        )
        receipt, writes = await chain.dispatch()
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert writes[(WTG, "active_power_setpoint")] == pytest.approx(7.0)
    finally:
        await chain.close()
