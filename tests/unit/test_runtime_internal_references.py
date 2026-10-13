"""Runtime 内部参考服务接线：先于规划执行发布，任务复位时清除事件状态。"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass

import pytest

from control.runtime import ControlRuntime, TaskState
from core.domain.config import OrcaConfig

pytestmark = pytest.mark.asyncio


def make_config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {"d1": {"type": "wind"}},
            "controllers": {"agc": {"type": "agc"}},
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
class FakeService:
    updates: int = 0
    resets: int = 0
    calls: list[str] | None = None

    async def update(self, now_ns: int) -> None:
        assert now_ns > 0
        self.updates += 1
        if self.calls is not None:
            self.calls.append("update")

    def reset(self) -> None:
        self.resets += 1


@dataclass
class FakePlanningCycle:
    calls: list[str] | None = None

    async def execute(self, task_id: str) -> object:
        if self.calls is not None:
            self.calls.append("plan")
        return {"ok": True}

    def reset(self, task_id: str) -> None:
        return None


async def test_internal_reference_runs_before_planning_and_resets_with_task() -> None:
    calls: list[str] = []
    service = FakeService(calls=calls)
    planning = FakePlanningCycle(calls=calls)
    runtime = ControlRuntime(
        make_config(), object(),
        planning_cycle=planning,
        planning_tasks=frozenset({"active"}),
    )
    runtime.install_internal_references({"active": service})  # type: ignore[arg-type]
    runtime.ready = True
    await runtime.start_task("active")
    await asyncio.sleep(0.035)
    await runtime.stop_task("active")
    assert service.updates >= 1
    # 每个周期：内部参考发布先于规划执行。
    pairs = list(itertools.pairwise(calls))
    assert ("update", "plan") in pairs
    # 停止后事件与算法状态复位，历史事件不得泄漏到下次启动。
    assert service.resets >= 1
    await runtime.start_task("active")
    await asyncio.sleep(0.015)
    await runtime.stop()
    assert service.updates >= 2


async def test_internal_reference_installation_requires_stopped_tasks() -> None:
    runtime = ControlRuntime(
        make_config(), object(),
        planning_cycle=FakePlanningCycle(),
        planning_tasks=frozenset({"active"}),
    )
    runtime.ready = True
    await runtime.start_task("active")
    try:
        with pytest.raises(RuntimeError, match="stopped"):
            runtime.install_internal_references({"active": FakeService()})  # type: ignore[arg-type]
        await runtime.stop_task("active")
        with pytest.raises(ValueError, match="planning task"):
            runtime.install_internal_references({"other": FakeService()})  # type: ignore[arg-type]
        runtime.install_internal_references({"active": FakeService()})  # type: ignore[arg-type]
        assert runtime.states["active"] == TaskState.STOPPED
    finally:
        await runtime.stop()


async def test_internal_reference_failure_skips_planning_and_resets() -> None:
    class FailingService(FakeService):
        async def update(self, now_ns: int) -> None:
            await super().update(now_ns)
            raise ValueError("stale frequency")

    service = FailingService()
    planning = FakePlanningCycle(calls=[])
    runtime = ControlRuntime(
        make_config(), object(),
        planning_cycle=planning,
        planning_tasks=frozenset({"active"}),
    )
    runtime.install_internal_references({"active": service})  # type: ignore[arg-type]
    runtime.ready = True
    await runtime.start_task("active")
    await asyncio.sleep(0.035)
    await runtime.stop()
    # 测点失效：规划不执行、原因记录、事件状态复位，且不产生任何下发。
    assert planning.calls == []
    assert "stale frequency" in runtime.reasons.get("active", "")
    assert service.resets >= 1
    assert not runtime.write_enabled
