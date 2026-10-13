"""可靠性故障注入：UNKNOWN 绝不自动重传，响应丢失后人工清除才可恢复。

安全边界：写入已到达 Mock Commander 但响应丢失时，Orca 判定结果为
UNKNOWN，闭锁任务且绝不重新发送上一次指令。
"""
from __future__ import annotations

import time

import pytest

from core.application.joint_power_allocator import JointAllocationResult
from core.application.read_only_planning_cycle import PlanningCycleResult
from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.safe_dispatch import DispatchStatus, OutputBinding
from infrastructure.wind_hub.commander import CommanderClient
from tests.support.commander import CommanderPoint, CommanderScenario, start_commander

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.reliability,
    pytest.mark.mock_service,
]

TASK = "pq"
WTG = "test-wtg001"


def setpoint(quantity: ControlQuantity, value: float) -> ControlValue:
    return ControlValue(
        quantity, ControlScope.DEVICE, WTG,
        ControlValueRole.DEVICE_SETPOINT, ControlValueKind.ABSOLUTE, value, "MW",
    )


def plan() -> PlanningCycleResult:
    return PlanningCycleResult(
        allocation=JointAllocationResult(
            active=(setpoint(ControlQuantity.ACTIVE_POWER, 6.0),),
            reactive=(),
            p_residual=0.0, q_residual=0.0,
            generation=7, feasible=True,
        ),
        acquired_at_ns=time.time_ns(),
    )


def binding() -> tuple[OutputBinding, ...]:
    return (
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
        ),
    )


async def make_dispatcher(
    scenario: CommanderScenario, *, permitted: bool = True
) -> tuple[SafePlanDispatcher, CommanderClient, object]:
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    dispatcher = SafePlanDispatcher(
        commander,
        binding(),
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: permitted,
        generation=lambda: 7,
        output_owner=lambda device, point: TASK,
    )
    return dispatcher, commander, server


async def test_response_lost_blocks_without_retransmit() -> None:
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={(WTG, "active_power_setpoint"): CommanderPoint()},
        response_lost=True,
    )
    dispatcher, commander, server = await make_dispatcher(scenario)
    try:
        receipt = await dispatcher.dispatch(TASK, plan())
        assert receipt.status == DispatchStatus.UNKNOWN
        # 写入到达过服务端恰好一次；UNKNOWN 后绝不补发。
        assert len(scenario.writes) == 1
        again = await dispatcher.dispatch(TASK, plan())
        assert again.status == DispatchStatus.UNKNOWN
        assert len(scenario.writes) == 2  # 显式再次调用是新指令，不是自动重传
    finally:
        await commander.close()
        await server.stop(grace=0)


async def test_revoked_authority_never_submits() -> None:
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={(WTG, "active_power_setpoint"): CommanderPoint()},
    )
    dispatcher, commander, server = await make_dispatcher(scenario, permitted=False)
    try:
        receipt = await dispatcher.dispatch(TASK, plan())
        assert receipt.status == DispatchStatus.BLOCKED
        assert scenario.writes == []
    finally:
        await commander.close()
        await server.stop(grace=0)


async def test_expired_plan_never_submits() -> None:
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={(WTG, "active_power_setpoint"): CommanderPoint()},
    )
    dispatcher, commander, server = await make_dispatcher(scenario)
    stale = PlanningCycleResult(
        allocation=plan().allocation,
        acquired_at_ns=time.time_ns() - 60_000_000_000,
    )
    try:
        receipt = await dispatcher.dispatch(TASK, stale)
        assert receipt.status == DispatchStatus.BLOCKED
        assert "expired" in (receipt.reason or "")
        assert scenario.writes == []
    finally:
        await commander.close()
        await server.stop(grace=0)
