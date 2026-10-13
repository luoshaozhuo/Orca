"""Commander 安全执行：最终门禁、整批校验、UNKNOWN 单次语义。"""
from __future__ import annotations

import time

import pytest

from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.application.read_only_planning_cycle import PlanningCycleResult
from core.application.joint_power_allocator import JointAllocationResult
from core.domain.control_value import (
    ControlQuantity, ControlScope, ControlValue,
    ControlValueKind, ControlValueRole,
)
from core.domain.safe_dispatch import DispatchStatus, OutputBinding


class Commander:
    def __init__(self, outcome: str = "CONFIRMED_SUCCESS") -> None:
        self.calls: list[tuple[str, str, str, float]] = []
        self.outcome = outcome

    async def write_once(
        self, command_id: str, device_id: str, point_id: str, value: float
    ) -> str:
        self.calls.append((command_id, device_id, point_id, value))
        if self.outcome == "EXCEPTION":
            raise TimeoutError("unknown write result")
        return self.outcome


def plan(
    *, generation: int = 3, second: bool = False,
    stale: bool = False,
) -> PlanningCycleResult:
    outputs = [
        ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.DEVICE,
            "wtg", ControlValueRole.DEVICE_SETPOINT,
            ControlValueKind.ABSOLUTE, 5.0, "MW",
        )
    ]
    if second:
        outputs.append(
            ControlValue(
                ControlQuantity.ACTIVE_POWER, ControlScope.DEVICE,
                "pv", ControlValueRole.DEVICE_SETPOINT,
                ControlValueKind.ABSOLUTE, 4.0, "MW",
            )
        )
    return PlanningCycleResult(
        allocation=JointAllocationResult(
            active=tuple(outputs),
            reactive=(),
            p_residual=0.0,
            q_residual=0.0,
            generation=generation,
            feasible=True,
        ),
        acquired_at_ns=time.time_ns() - (5_000_000_000 if stale else 1_000_000),
    )


def dispatcher(
    commander: Commander,
    *,
    allowed=lambda task: True,
    generation=lambda: 3,
    owner=lambda device, point: "agc",
) -> SafePlanDispatcher:
    return SafePlanDispatcher(
        commander,
        (
            OutputBinding("wtg", ControlQuantity.ACTIVE_POWER, "p_cmd", 0.0, 10.0),
            OutputBinding("pv", ControlQuantity.ACTIVE_POWER, "p_cmd", 0.0, 10.0),
        ),
        maximum_age_ns=2_000_000_000,
        permitted=allowed,
        generation=generation,
        output_owner=owner,
    )


@pytest.mark.asyncio
async def test_valid_plan_submits_each_point_exactly_once() -> None:
    commander = Commander()
    receipt = await dispatcher(commander).dispatch("agc", plan(second=True))
    assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
    assert receipt.confirmed == 2
    assert len(commander.calls) == 2
    assert len(set(receipt.command_ids)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["UNKNOWN", "EXCEPTION", "CONFIRMED_FAILURE"])
async def test_non_success_does_not_retry_or_send_remaining_points(outcome: str) -> None:
    commander = Commander(outcome)
    receipt = await dispatcher(commander).dispatch("agc", plan(second=True))
    assert receipt.status != DispatchStatus.CONFIRMED_SUCCESS
    assert len(commander.calls) == 1


@pytest.mark.asyncio
async def test_expired_generation_and_revoked_authority_do_not_write() -> None:
    for kwargs, candidate in (
        ({}, plan(stale=True)),
        ({"generation": lambda: 4}, plan()),
        ({"allowed": lambda task: False}, plan()),
    ):
        commander = Commander()
        result = await dispatcher(commander, **kwargs).dispatch("agc", candidate)
        assert result.status == DispatchStatus.BLOCKED
        assert commander.calls == []


@pytest.mark.asyncio
async def test_ownership_and_mapping_are_checked_before_first_write() -> None:
    commander = Commander()
    result = await dispatcher(
        commander,
        owner=lambda device, point: "other" if device == "pv" else "agc",
    ).dispatch("agc", plan(second=True))
    assert result.status == DispatchStatus.BLOCKED
    assert not commander.calls


@pytest.mark.asyncio
async def test_recheck_authority_before_each_command() -> None:
    commander = Commander()
    checks = iter((True, True, False))
    result = await dispatcher(
        commander, allowed=lambda task: next(checks),
    ).dispatch("agc", plan(second=True))
    assert result.status == DispatchStatus.BLOCKED
    assert len(commander.calls) == 1
    assert result.confirmed == 1
