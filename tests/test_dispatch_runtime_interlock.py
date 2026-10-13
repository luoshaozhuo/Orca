"""故障后持续闭锁与人工清除的 Runtime 生命周期测试。"""
from __future__ import annotations

import pytest

from control.runtime import ControlRuntime
from core.domain.config import OrcaConfig


def config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {"wtg": {"type": "wind"}},
            "controllers": {"agc": {"type": "agc", "strategy": "noop"}},
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 0.01,
                    "resource_ids": ["wtg"],
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_dispatch_block_survives_task_stop_and_requires_clearance() -> None:
    runtime = ControlRuntime(config(), object())
    runtime.ready = True
    runtime.dispatch_blocked["active"] = "dispatch UNKNOWN"
    with pytest.raises(RuntimeError, match="dispatch blocked"):
        await runtime.start_task("active")
    await runtime.stop_task("active")
    assert runtime.dispatch_blocked["active"] == "dispatch UNKNOWN"
    with pytest.raises(PermissionError):
        runtime.clear_dispatch_block("active", authorized=False)
    runtime.clear_dispatch_block("active", authorized=True)
    assert not runtime.dispatch_blocked


def test_clearance_is_rejected_while_running() -> None:
    runtime = ControlRuntime(config(), object())
    runtime.states["active"] = "RUNNING"
    with pytest.raises(RuntimeError, match="stopped task"):
        runtime.clear_dispatch_block("active", authorized=True)


def test_dispatcher_installation_requires_stopped_tasks() -> None:
    runtime = ControlRuntime(config(), object())
    runtime.states["active"] = "RUNNING"
    with pytest.raises(RuntimeError, match="stopped tasks"):
        runtime.install_dispatcher(object())  # type: ignore[arg-type]


def test_dispatcher_installation_fails_if_writes_enabled() -> None:
    runtime = ControlRuntime(config(), object())
    runtime.write_enabled = True
    with pytest.raises(RuntimeError, match="disabled writes"):
        runtime.install_dispatcher(object())  # type: ignore[arg-type]
