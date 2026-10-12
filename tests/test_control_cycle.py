"""控制周期前置校验、写入门禁和单次指令语义。"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from control.cycle import ControlCycle
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.config.yaml import load_config

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "template"


class StaticSource:
    async def latest(self, pairs):
        return {
            pair: {"value": 1.0, "quality": "good", "timestamp": datetime.now(UTC).isoformat()}
            for pair in pairs
        }


class FakeCommander:
    def __init__(self, outcome: str = "CONFIRMED_SUCCESS") -> None:
        self.calls: list[tuple[str, str, str, float]] = []
        self.outcome = outcome

    async def write_once(self, command_id, device_id, point_id, value):
        self.calls.append((command_id, device_id, point_id, value))
        return self.outcome


class FixedStrategy:
    def solve(self, measurements):
        return ControlDecision((Setpoint("wtg001", "active_power_setpoint", 2.0),))


@pytest.mark.asyncio
async def test_write_gate_rechecked_after_strategy():
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    checks = iter((True, False))
    reason = await cycle.execute("active_power", can_write=lambda: next(checks))
    assert reason is not None and "dependency state changed" in reason
    assert commander.calls == []


@pytest.mark.asyncio
async def test_invalid_input_never_calls_strategy():
    class InvalidSource(StaticSource):
        async def latest(self, pairs):
            result = await super().latest(pairs)
            for item in result.values():
                item["value"] = float("nan")
            return result

    class Forbidden:
        def solve(self, measurements):
            raise AssertionError("invalid input reached strategy")

    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), InvalidSource(), commander, Forbidden())
    reason = await cycle.execute("active_power", allow_write=True)
    assert reason is not None and "non-finite input" in reason
    assert not commander.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["CONFIRMED_FAILURE", "UNKNOWN"])
async def test_non_success_is_never_retried(outcome: str):
    commander = FakeCommander(outcome)
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    reason = await cycle.execute("active_power", allow_write=True)
    assert reason is not None and outcome in reason
    assert len(commander.calls) == 1
    assert commander.calls[0][0]
