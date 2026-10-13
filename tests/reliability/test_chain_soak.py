"""链路级短时浸泡：真实 Redis + Mock Commander 下资源有界、无泄漏。

PR 快速层：300 个完整「采集 → 规划 → 分配 → 下发」周期，监控内存、
文件描述符、asyncio 任务数、延迟分布、UNKNOWN 计数与指令计数。
中长层（分钟级）与夜间层（小时级）需专用环境另行触发，不在 CI
伪造为已执行。
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from core.application.active_power_controller import ActivePowerController
from core.application.control_coordinator import ControlCoordinator
from core.application.control_pipeline_factory import build_control_pipeline
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.application.reference_manager import ReferenceManager
from core.application.safe_plan_dispatcher import SafePlanDispatcher
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
    RedisPlanningSnapshotSource,
)
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
from tests.support.commander import CommanderPoint, CommanderScenario, start_commander
from tests.support.redis_data import DataPublisher
from tests.support.redis_data import test_prefix as make_test_prefix

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.reliability,
    pytest.mark.soak,
    pytest.mark.real_service,
    pytest.mark.mock_service,
]

GENERATION = 7
TASK = "soak"
POI = "test-poi-soak"
DISPATCH = "test-dispatch-soak"
WTG = "test-wtg-soak"
ITERATIONS = 300
WARMUP = 50


def fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def rss_kb() -> int:
    with open("/proc/self/status", encoding="utf-8") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("VmRSS not found")


async def test_chain_soak_resources_bounded_no_leak(
    redis_config, data_publisher: DataPublisher,
) -> None:
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
                maximum=10.0,
                dt_s=1.0,
                devices=(
                    DevicePlanningBinding(
                        device_id=WTG, p_point="p", q_point="q",
                        technology=DeviceTechnology.WIND,
                        p_min=0.0, p_max=10.0, q_min=-5.0, q_max=5.0,
                        s_max=11.0, p_auto=True, q_auto=False,
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
        can_plan=lambda task, device, quantity: True,
    )
    cycle = ReadOnlyPlanningCycle(
        snapshot_source,
        {
            TASK: ActivePowerController(
                pipeline=build_control_pipeline(ControlPipelineConfig()),
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
            (WTG, "q"): CommanderPoint(writable=False),
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
                point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
            ),
        ),
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: True,
        generation=lambda: GENERATION,
        output_owner=lambda device, point: TASK,
    )

    async def publish() -> None:
        for device, point, value in (
            (POI, "p", 5.0), (WTG, "p", 5.0), (WTG, "q", 0.0),
            (DISPATCH, "p_ref", 6.0),
        ):
            await data_publisher.publish(device, point, value)

    latencies: list[float] = []
    unknown = 0
    baseline_tasks = len(asyncio.all_tasks())
    try:
        for iteration in range(ITERATIONS):
            started = time.monotonic()
            await publish()
            result = await cycle.execute(TASK)
            assert result.allocation.feasible
            receipt = await dispatcher.dispatch(TASK, result)
            if receipt.status != DispatchStatus.CONFIRMED_SUCCESS:
                unknown += 1
            latencies.append(time.monotonic() - started)
            if iteration + 1 == WARMUP:
                warmup_fd = fd_count()
                warmup_rss = rss_kb()
                warmup_tasks = len(asyncio.all_tasks())
        # 资源有界：预热后 FD/RSS/任务数不得持续增长。
        assert fd_count() <= warmup_fd + 2
        assert rss_kb() - warmup_rss < 16 * 1024
        assert len(asyncio.all_tasks()) <= warmup_tasks + 1
        # 每个周期恰好一条指令：无重传、无 UNKNOWN。
        assert unknown == 0
        assert len(scenario.writes) == ITERATIONS
        # 延迟分布有界（宽松门限，CI 稳定；量级异常即失败）。
        ordered = sorted(latencies[WARMUP:])
        p99 = ordered[int(len(ordered) * 0.99) - 1]
        mean = sum(ordered) / len(ordered)
        assert mean < 0.5
        assert p99 < 2.0
    finally:
        await commander.close()
        await source.close()
        await server.stop(grace=0)
    # 关闭后无连接/任务残留。
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= baseline_tasks + 1
