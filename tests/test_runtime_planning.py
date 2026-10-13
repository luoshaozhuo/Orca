"""Runtime 只读规划分支测试，严禁通过旧写指令链。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from control.runtime import ControlRuntime
from core.domain.config import OrcaConfig


def make_config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {"d1": {"type": "wind"}},
            "controllers": {"agc": {"type": "agc", "strategy": "noop"}},
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 0.01,
                    "resource_ids": ["d1"],
                },
            },
        }
    )


@dataclass
class FakePlanningCycle:
    count: int = 0
    reset_count: int = 0
    fail: bool = False

    async def execute(self, task_id: str) -> object:
        assert task_id == "active"
        self.count += 1
        if self.fail:
            raise ValueError("invalid planning snapshot")
        return {"revision": self.count}

    def reset(self, task_id: str) -> None:
        self.reset_count += 1


class ForbiddenLegacyCycle:
    async def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("legacy command path must not execute")


@pytest.mark.asyncio
async def test_runtime_planning_never_enters_legacy_commander_path() -> None:
    planning = FakePlanningCycle()
    runtime = ControlRuntime(
        make_config(),
        object(),
        ForbiddenLegacyCycle(),  # type: ignore[arg-type]
        planning_cycle=planning,
        planning_tasks=frozenset({"active"}),
    )
    runtime.ready = True
    await runtime.start_task("active")
    await asyncio.sleep(0.045)
    assert planning.count >= 1
    assert "active" in runtime.planning_results
    assert not runtime.write_enabled
    await runtime.stop_task("active")
    assert "active" not in runtime.planning_results
    assert planning.reset_count >= 2


@pytest.mark.asyncio
async def test_failure_invalidates_existing_result_and_algorithm_state() -> None:
    planning = FakePlanningCycle()
    runtime = ControlRuntime(
        make_config(), object(),
        planning_cycle=planning,
        planning_tasks=frozenset({"active"}),
    )
    runtime.ready = True
    await runtime.start_task("active")
    await asyncio.sleep(0.023)
    assert "active" in runtime.planning_results
    planning.fail = True
    await asyncio.sleep(0.033)
    assert "active" not in runtime.planning_results
    assert "invalid planning snapshot" in runtime.reasons["active"]
    await runtime.stop_task("active")


def test_planning_tasks_require_explicit_port() -> None:
    with pytest.raises(ValueError, match="require a planning cycle"):
        ControlRuntime(
            make_config(), object(),
            planning_tasks=frozenset({"active"}),
        )
