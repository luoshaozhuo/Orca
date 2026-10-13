"""Runtime 控制功能准入：禁止绕过投退、闭锁和人工控制权。"""
from __future__ import annotations

from pathlib import Path

import pytest

from control.runtime import ControlRuntime
from core.domain.control_authority import AuthorityMode, ControlFunction
from infrastructure.config.yaml import load_config

CONFIG = Path(__file__).resolve().parents[1] / "configs"


@pytest.mark.asyncio
async def test_function_service_and_blocks_gate_runtime_writes() -> None:
    runtime = ControlRuntime(load_config(CONFIG), object())
    runtime.ready = True
    runtime.write_enabled = True
    await runtime.start_task("active_power")
    try:
        assert not runtime._can_write_task("active_power")
        runtime.control_authority.put_in_service(ControlFunction.AGC, authorized=True)
        assert runtime._can_write_task("active_power")
        runtime.control_authority.block(ControlFunction.AGC, "BAD_DATA")
        assert not runtime._can_write_task("active_power")
        runtime.control_authority.clear_block(ControlFunction.AGC, "BAD_DATA")
        assert not runtime._can_write_task("active_power")
        runtime.control_authority.put_in_service(ControlFunction.AGC, authorized=True)
        assert runtime._can_write_task("active_power")
        runtime.ready = False
        assert not runtime._can_write_task("active_power")
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_manual_handover_requires_auto_functions_out_and_releases_ownership() -> None:
    runtime = ControlRuntime(load_config(CONFIG), object())
    runtime.ready = True
    await runtime.start_task("active_power")
    runtime.control_authority.put_in_service(ControlFunction.AGC, authorized=True)
    with pytest.raises(RuntimeError, match="out of service"):
        await runtime.enter_manual(authorized=True)
    assert runtime.states["active_power"] == "RUNNING"
    runtime.control_authority.take_out_of_service(ControlFunction.AGC)
    with pytest.raises(PermissionError):
        await runtime.enter_manual(authorized=False)
    await runtime.enter_manual(authorized=True)
    assert runtime.control_authority.mode == AuthorityMode.MANUAL
    assert runtime.states["active_power"] == "STOPPED"
    assert runtime._authority == {}
    runtime.control_authority.exit_manual(authorized=True)
    assert not runtime._can_write_task("active_power")
