"""Runtime 准入门禁、输出归属冲突与控制功能推导分支。"""
from __future__ import annotations

import asyncio

import pytest

from control.runtime import ControlRuntime, TaskState
from core.domain.config import OrcaConfig
from core.domain.control_authority import ControlFunction


def config(task_overrides: dict | None = None, controller_type: str = "agc") -> OrcaConfig:
    tasks = {
        "active": {
            "controller_id": "agc",
            "interval": 0.01,
            "resource_ids": ["wtg"],
        },
    }
    if task_overrides:
        tasks.update(task_overrides)
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {
                "wtg": {
                    "type": "wind",
                    "outputs": {
                        "setpoint": {
                            "point_id": "active_power_setpoint",
                            "minimum": 0.0,
                            "maximum": 10.0,
                        },
                    },
                },
                "wtg2": {
                    "type": "wind",
                    "outputs": {
                        "setpoint": {
                            "point_id": "active_power_setpoint",
                            "minimum": 0.0,
                            "maximum": 10.0,
                        },
                    },
                },
            },
            "controllers": {"agc": {"type": controller_type}},
            "tasks": tasks,
        }
    )


def test_planning_tasks_require_cycle_and_known_task() -> None:
    with pytest.raises(ValueError, match="planning cycle"):
        ControlRuntime(config(), object(), planning_tasks=frozenset({"active"}))
    with pytest.raises(ValueError, match="unknown planning task"):
        ControlRuntime(
            config(), object(),
            planning_cycle=object(),  # type: ignore[arg-type]
            planning_tasks=frozenset({"ghost"}),
        )


@pytest.mark.asyncio
async def test_start_task_rejects_unknown_disabled_and_not_ready() -> None:
    runtime = ControlRuntime(config(), object())
    with pytest.raises(ValueError, match="unknown task"):
        await runtime.start_task("ghost")
    with pytest.raises(RuntimeError, match="not verified"):
        await runtime.start_task("active")

    disabled = ControlRuntime(
        config(task_overrides={
            "inactive": {
                "controller_id": "agc",
                "interval": 0.01,
                "enabled": False,
                "resource_ids": ["wtg"],
            },
        }),
        object(),
    )
    disabled.ready = True
    with pytest.raises(ValueError, match="disabled task"):
        await disabled.start_task("inactive")


@pytest.mark.asyncio
async def test_output_ownership_conflict_rejects_second_task() -> None:
    runtime = ControlRuntime(
        config(task_overrides={
            "second": {
                "controller_id": "agc",
                "interval": 0.01,
                "resource_ids": ["wtg"],
            },
        }),
        object(),
    )
    runtime.ready = True
    assert runtime.output_owner("wtg", "active_power_setpoint") is None
    await runtime.start_task("active")
    try:
        assert runtime.output_owner("wtg", "active_power_setpoint") == "active"
        assert runtime.output_owners() == {
            ("wtg", "active_power_setpoint"): "active"
        }
        with pytest.raises(RuntimeError, match="already owned"):
            await runtime.start_task("second")
        # 重复启动是幂等空操作，不抛错也不重置归属。
        await runtime.start_task("active")
        assert runtime.output_owner("wtg", "active_power_setpoint") == "active"
    finally:
        await runtime.stop()
    assert runtime.output_owners() == {}


@pytest.mark.asyncio
async def test_read_only_cycle_failure_is_isolated_and_reported() -> None:
    runtime = ControlRuntime(config(), object())
    runtime.ready = True
    await runtime.start_task("active")
    try:
        # 数据源不可用：周期异常被隔离为原因记录，任务保持运行。
        for _ in range(50):
            if "cycle failed" in runtime.reasons.get("active", ""):
                break
            await asyncio.sleep(0.01)
        assert "cycle failed" in runtime.reasons.get("active", "")
        assert runtime.states["active"] == TaskState.RUNNING
    finally:
        await runtime.stop()
    assert runtime.states["active"] == TaskState.STOPPED


def test_task_functions_derive_from_controller_type() -> None:
    runtime = ControlRuntime(config(), object())
    assert runtime.task_functions("active") == frozenset({ControlFunction.AGC})
    # 未识别的控制器类型不得臆造功能，返回空集合并由投退流程拒绝。
    unknown = ControlRuntime(config(controller_type="mystery"), object())
    assert unknown.task_functions("active") == frozenset()


def test_planning_accessors_and_write_gate() -> None:
    runtime = ControlRuntime(config(), object())
    assert runtime.planning_cycle_port is None
    assert runtime.planning_task_ids == frozenset()
    # 功能未投运：写门禁关闭。
    assert not runtime._can_write_task("active")


@pytest.mark.asyncio
async def test_stop_task_preserves_other_task_ownership() -> None:
    cfg = config(
        task_overrides={
            "second": {
                "controller_id": "agc",
                "interval": 0.01,
                "resource_ids": ["wtg2"],
            },
        }
    )
    runtime = ControlRuntime(cfg, object())
    runtime.ready = True
    await runtime.start_task("active")
    await runtime.start_task("second")
    try:
        await runtime.stop_task("second")
        # 停止 second 只释放其自身输出归属，active 的归属保持不变。
        assert runtime.output_owner("wtg", "active_power_setpoint") == "active"
        assert runtime.output_owner("wtg2", "active_power_setpoint") is None
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_stop_task_unknown_and_never_started() -> None:
    runtime = ControlRuntime(config(), object())
    with pytest.raises(ValueError, match="unknown task"):
        await runtime.stop_task("ghost")
    # 从未启动的任务停止是安全空操作。
    await runtime.stop_task("active")
    assert runtime.states["active"] == TaskState.STOPPED


def test_task_functions_from_planning_and_reactive() -> None:
    cfg = OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {
                "poi": {
                    "type": "poi",
                    "inputs": {
                        "p": {"point_id": "p"},
                        "q": {"point_id": "q"},
                        "frequency": {"point_id": "freq"},
                    },
                },
                "dispatch": {
                    "type": "dispatch",
                    "inputs": {"q_ref": {"point_id": "q_ref", "required": False}},
                },
                "wtg": {
                    "type": "wind",
                    "inputs": {
                        "p": {"point_id": "p"},
                        "q": {"point_id": "q"},
                    },
                },
            },
            "controllers": {
                "agc": {
                    "type": "pq",
                    "references": [
                        {"purpose": "ACTIVE_BASE", "target": "POI", "primary": "GRID"},
                        {"purpose": "REACTIVE_BASE", "target": "POI", "primary": "GRID"},
                        {"purpose": "PFR_DELTA", "target": "POI", "primary": "INTERNAL"},
                    ],
                },
            },
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 1.0,
                    "resource_ids": ["wtg"],
                    "planning": {
                        "target": "POI",
                        "poi_device_id": "poi",
                        "poi_active_point": "p",
                        "minimum": 0.0,
                        "maximum": 10.0,
                        "authorized_sources": ["GRID", "INTERNAL"],
                        "active_functions": ["pfr"],
                        "frequency": {
                            "frequency_point": "freq",
                            "nominal_frequency_hz": 50.0,
                            "valid_for_s": 3.0,
                            "pfr": {
                                "deadband_hz": 0.05,
                                "gain_mw_per_hz": 10.0,
                                "max_increase_mw": 6.0,
                                "max_decrease_mw": 10.0,
                            },
                        },
                        "devices": [
                            {
                                "device_id": "wtg",
                                "technology": "wind",
                                "p_point": "p",
                                "q_point": "q",
                                "p_min": 0.0,
                                "p_max": 10.0,
                                "q_min": -5.0,
                                "q_max": 5.0,
                                "s_max": 10.0,
                                "p_auto": True,
                            }
                        ],
                    },
                    "reactive_planning": {
                        "target": "POI",
                        "mode": "REACTIVE_POWER",
                        "poi_device_id": "poi",
                        "poi_reactive_point": "q",
                        "minimum": -5.0,
                        "maximum": 5.0,
                        "authorized_sources": ["GRID"],
                        "reference_inputs": [
                            {
                                "purpose": "REACTIVE_BASE",
                                "source": "GRID",
                                "resource_id": "dispatch",
                                "point_id": "q_ref",
                                "valid_for_s": 60.0,
                            }
                        ],
                    },
                },
            },
        }
    )
    runtime = ControlRuntime(cfg, object())
    assert runtime.task_functions("active") == frozenset(
        {ControlFunction.AGC, ControlFunction.AVC, ControlFunction.PFR}
    )


@pytest.mark.asyncio
async def test_read_only_validation_errors_set_and_clear_reasons() -> None:
    from datetime import UTC, datetime

    cfg = OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {
                "wtg": {
                    "type": "wind",
                    "inputs": {"p": {"point_id": "p"}},
                },
            },
            "controllers": {"agc": {"type": "agc"}},
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 0.01,
                    "resource_ids": ["wtg"],
                },
            },
        }
    )

    class FakeSource:
        def __init__(self) -> None:
            self.records: dict = {
                ("wtg", "p"): {
                    "value": 5.0,
                    "quality": "bad",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            }

        async def latest(self, pairs: list) -> dict:
            return self.records

    resets: list[str] = []
    source = FakeSource()
    runtime = ControlRuntime(
        cfg, source, reset_control_state=resets.append,
    )
    runtime.ready = True
    await runtime.start_task("active")
    try:
        for _ in range(100):
            if "bad quality" in runtime.reasons.get("active", ""):
                break
            await asyncio.sleep(0.01)
        assert "bad quality" in runtime.reasons["active"]
        assert resets  # 输入失效：算法状态被复位

        # 输入恢复：原因清除，任务继续运行。
        source.records = {
            ("wtg", "p"): {
                "value": 5.0,
                "quality": "good",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }
        for _ in range(100):
            if "active" not in runtime.reasons:
                break
            await asyncio.sleep(0.01)
        assert "active" not in runtime.reasons
        assert runtime.states["active"] == TaskState.RUNNING
    finally:
        await runtime.stop()
