"""输出绑定校验与 SafePlanDispatcher 门禁原因分支补齐。"""
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

TASK = "pq"
WTG = "test-wtg001"


def binding(**overrides: object) -> OutputBinding:
    values = {
        "device_id": WTG,
        "quantity": ControlQuantity.ACTIVE_POWER,
        "point_id": "active_power_setpoint",
        "minimum": 0.0,
        "maximum": 10.0,
    }
    values.update(overrides)
    return OutputBinding(**values)  # type: ignore[arg-type]


def test_output_binding_validation() -> None:
    with pytest.raises(ValueError, match="required"):
        binding(device_id="")
    with pytest.raises(ValueError, match="unsupported dispatch quantity"):
        binding(quantity=ControlQuantity.FREQUENCY)
    with pytest.raises(ValueError, match="finite"):
        binding(maximum=float("nan"))
    with pytest.raises(ValueError, match="inverted"):
        binding(minimum=10.0, maximum=0.0)


def setpoint(value: float) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.DEVICE, WTG,
        ControlValueRole.DEVICE_SETPOINT, ControlValueKind.ABSOLUTE, value, "MW",
    )


def plan(
    *, generation: int = 7, feasible: bool = True, now_ns: int | None = None
) -> PlanningCycleResult:
    return PlanningCycleResult(
        allocation=JointAllocationResult(
            active=(setpoint(6.0),), reactive=(),
            p_residual=0.0, q_residual=0.0,
            generation=generation, feasible=feasible,
        ),
        acquired_at_ns=now_ns if now_ns is not None else time.time_ns(),
    )


class RecordingCommander:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str, float]] = []

    async def write_once(
        self, command_id: str, device_id: str, point_id: str, value: float
    ) -> str:
        self.writes.append((command_id, device_id, point_id, value))
        return "CONFIRMED_SUCCESS"


def make_dispatcher(
    commander: RecordingCommander,
    *,
    generation: int = 7,
    owner_changes: bool = False,
) -> SafePlanDispatcher:
    calls = {"count": 0}

    def owner(device: str, point: str) -> str | None:
        calls["count"] += 1
        # 预检通过后、批次内逐条复检时归属被收回。
        if owner_changes and calls["count"] > 1:
            return "other-task"
        return TASK

    return SafePlanDispatcher(
        commander,  # type: ignore[arg-type]
        (binding(),),
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: True,
        generation=lambda: generation,
        output_owner=owner,
    )


@pytest.mark.asyncio
async def test_infeasible_allocation_blocked() -> None:
    commander = RecordingCommander()
    dispatcher = make_dispatcher(commander)
    receipt = await dispatcher.dispatch(TASK, plan(feasible=False))
    assert receipt.status == DispatchStatus.BLOCKED
    assert "infeasible" in receipt.reason
    assert commander.writes == []


@pytest.mark.asyncio
async def test_generation_mismatch_blocked() -> None:
    commander = RecordingCommander()
    dispatcher = make_dispatcher(commander, generation=8)
    receipt = await dispatcher.dispatch(TASK, plan(generation=7))
    assert receipt.status == DispatchStatus.BLOCKED
    assert "generation" in receipt.reason
    assert commander.writes == []


@pytest.mark.asyncio
async def test_future_snapshot_blocked() -> None:
    commander = RecordingCommander()
    dispatcher = make_dispatcher(commander)
    receipt = await dispatcher.dispatch(
        TASK, plan(now_ns=time.time_ns() + 60_000_000_000)
    )
    assert receipt.status == DispatchStatus.BLOCKED
    assert "future" in receipt.reason
    assert commander.writes == []


@pytest.mark.asyncio
async def test_empty_plan_blocked() -> None:
    commander = RecordingCommander()
    dispatcher = make_dispatcher(commander)
    empty = PlanningCycleResult(
        allocation=JointAllocationResult(
            active=(), reactive=(),
            p_residual=0.0, q_residual=0.0,
            generation=7, feasible=True,
        ),
        acquired_at_ns=time.time_ns(),
    )
    receipt = await dispatcher.dispatch(TASK, empty)
    assert receipt.status == DispatchStatus.BLOCKED
    assert "empty" in receipt.reason
    assert commander.writes == []


@pytest.mark.asyncio
async def test_ownership_change_mid_batch_stops_without_writes() -> None:
    commander = RecordingCommander()
    dispatcher = make_dispatcher(commander, owner_changes=True)
    receipt = await dispatcher.dispatch(TASK, plan())
    assert receipt.status == DispatchStatus.BLOCKED
    assert "ownership" in receipt.reason
    assert commander.writes == []
