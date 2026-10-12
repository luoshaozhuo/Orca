"""UC2：输入有效性、输出门禁、失败及不重传契约测试。"""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from control.cycle import ControlCycle
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.config.yaml import load_config

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "template"


class StaticSource:
    def __init__(self, *, age: float = 0.0, quality: str = "good") -> None:
        self.age = age
        self.quality = quality

    async def latest(self, pairs):
        return {
            pair: {
                "value": 2.0,
                "quality": self.quality,
                "timestamp": (datetime.now(UTC) - timedelta(seconds=self.age)).isoformat(),
            }
            for pair in pairs
        }


class FixedStrategy:
    def solve(self, measurements):
        assert measurements["wtg001"]["active_power"] == 2.0
        return ControlDecision((Setpoint("wtg001", "active_power_setpoint", 1.0),))


class FakeCommander:
    def __init__(self, outcome="CONFIRMED_SUCCESS"):
        self.calls = []
        self.outcome = outcome

    async def write_once(self, command_id, device_id, point_id, value):
        self.calls.append((command_id, device_id, point_id, value))
        return self.outcome


@pytest.mark.asyncio
async def test_stale_never_calls_strategy_or_commander():
    class ForbiddenStrategy:
        def solve(self, measurements):
            raise AssertionError("stale input reached strategy")
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(age=30), commander, ForbiddenStrategy())
    assert "stale" in await cycle.execute("active_power", allow_write=True)
    assert not commander.calls


@pytest.mark.asyncio
async def test_write_is_disabled_until_authorized():
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    assert "output disabled" in await cycle.execute("active_power")
    assert commander.calls == []


@pytest.mark.asyncio
async def test_one_submission_on_unknown_outcome():
    commander = FakeCommander("UNKNOWN")
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    assert "UNKNOWN" in await cycle.execute("active_power", allow_write=True)
    assert len(commander.calls) == 1
    assert commander.calls[0][1:] == ("wtg001", "active_power_setpoint", 1.0)


@pytest.mark.asyncio
async def test_success_one_submission():
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    assert await cycle.execute("active_power", allow_write=True) is None
    assert len(commander.calls) == 1


@pytest.mark.asyncio
async def test_bad_quality_skips_command():
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(quality="bad"), commander, FixedStrategy())
    assert "bad quality" in await cycle.execute("active_power", allow_write=True)
    assert commander.calls == []
