"""控制任务的有状态算法生命周期保护。"""
from __future__ import annotations

import asyncio

import pytest

from control.runtime import ControlRuntime
from core.domain.config import OrcaConfig


def config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {"device": {"type": "wind"}},
            "controllers": {"agc": {"type": "agc"}},
            "tasks": {
                "task": {
                    "controller_id": "agc",
                    "interval": 0.01,
                    "resource_ids": ["device"],
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_task_start_stop_resets_algorithm_state() -> None:
    resets: list[str] = []
    runtime = ControlRuntime(
        config(),
        object(),
        reset_control_state=resets.append,
    )
    runtime.ready = True
    await runtime.start_task("task")
    await asyncio.sleep(0)
    await runtime.stop_task("task")
    assert resets.count("task") >= 2
    assert runtime.states["task"] == "STOPPED"


@pytest.mark.asyncio
async def test_failed_dependencies_reset_algorithm_state() -> None:
    resets: list[str] = []
    runtime = ControlRuntime(
        config(),
        object(),
        reset_control_state=resets.append,
    )
    runtime.ready = True
    await runtime.start_task("task")
    runtime.ready = False
    await asyncio.sleep(0.03)
    await runtime.stop_task("task")
    assert "task" in resets
    assert runtime.output_owner("device", "point") is None
