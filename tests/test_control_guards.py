"""输出互斥和约束验证：纯内存测试，不连接现场。"""
from pathlib import Path

import pytest

from control.decision import validate_decision
from control.runtime import ControlRuntime
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.config.yaml import load_config

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "template"


def test_setpoint_limits():
    config = load_config(CONFIG)
    ok = ControlDecision((Setpoint("wtg001", "active_power_setpoint", 4.0),))
    bad = ControlDecision((Setpoint("wtg001", "active_power_setpoint", 6.0),))
    assert not validate_decision(config, "active_power", ok)
    assert "above maximum" in validate_decision(config, "active_power", bad)[0]


def test_duplicate_output_rejected():
    config = load_config(CONFIG)
    decision = ControlDecision((
        Setpoint("wtg001", "active_power_setpoint", 2.0),
        Setpoint("wtg001", "active_power_setpoint", 3.0),
    ))
    assert any("duplicate output" in item for item in validate_decision(config, "active_power", decision))


@pytest.mark.asyncio
async def test_tasks_cannot_own_same_physical_output():
    config = load_config(CONFIG)
    raw = config.model_dump()
    raw["tasks"]["second"] = dict(raw["tasks"]["active_power"])
    from core.domain.config import OrcaConfig

    runtime = ControlRuntime(OrcaConfig.model_validate(raw), object())
    runtime.ready = True
    await runtime.start_task("active_power")
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            await runtime.start_task("second")
    finally:
        await runtime.stop_task("active_power")
    assert not runtime._authority
    await runtime.start_task("second")
    await runtime.stop()
