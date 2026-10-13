"""看门狗故障、重连与配置变化重校验分支（假端口，无网络）。"""
from __future__ import annotations

import asyncio

import pytest

from control.runtime import ControlRuntime
from core.domain.config import OrcaConfig
from infrastructure.wind_hub.watchdog import WindHubWatchdog

pytestmark = pytest.mark.asyncio


def config(interval: float = 0.01) -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {
                "collector": {"target": "127.0.0.1:50051"},
                "watchdog": {"interval": interval, "timeout": 0.5},
            },
            "resources": {
                "wtg": {
                    "type": "wind",
                    "inputs": {"p": {"point_id": "p", "data_type": "float32"}},
                    "outputs": {
                        "sp": {
                            "point_id": "active_power_setpoint",
                            "minimum": 0.0,
                            "maximum": 10.0,
                        },
                    },
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


class FakeRedis:
    def __init__(self) -> None:
        self.fail_ping = False
        self.fail_connect = False
        self.connects = 0

    async def ping(self) -> None:
        if self.fail_ping:
            raise ConnectionError("redis down")

    async def connect(self) -> None:
        self.connects += 1
        if self.fail_connect:
            raise ConnectionError("redis reconnect failed")

    async def close(self) -> None:
        return None


class FakeCommander:
    def __init__(self) -> None:
        self.running = True
        self.revision = "rev-a"
        self.fail_status = False
        self.fail_connect = True
        self.devices_set = {"wtg"}
        self.types = {"p": "float32", "active_power_setpoint": "float32"}
        self.connects = 0

    async def status(self) -> tuple[bool, str]:
        if self.fail_status:
            raise ConnectionError("commander down")
        return self.running, self.revision

    async def devices(self) -> set[str]:
        return set(self.devices_set)

    async def resolve_point(self, device_id: str, point_id: str) -> str:
        return self.types.get(point_id, "float32")

    async def connect(self) -> None:
        self.connects += 1
        if self.fail_connect:
            raise ConnectionError("commander reconnect failed")

    async def close(self) -> None:
        return None


def make_watchdog(interval: float = 0.01):
    cfg = config(interval)
    runtime = ControlRuntime(cfg, object())
    redis = FakeRedis()
    commander = FakeCommander()
    commander.fail_connect = False
    return WindHubWatchdog(cfg, runtime, redis, commander), runtime, redis, commander


async def test_healthy_check_marks_ready() -> None:
    watchdog, runtime, _, _ = make_watchdog()
    await watchdog.check_once()
    assert watchdog.redis_ok and watchdog.commander_ok
    assert watchdog.point_mappings_ok
    assert runtime.ready
    # 重复巡检不清除就绪状态。
    await watchdog.check_once()
    assert runtime.ready


async def test_redis_failure_blocks_ready_and_recovers() -> None:
    watchdog, runtime, redis, _ = make_watchdog()
    await watchdog.check_once()
    assert runtime.ready
    redis.fail_ping = True
    redis.fail_connect = True
    await watchdog.check_once()
    assert not watchdog.redis_ok
    assert not runtime.ready
    assert "Redis" in (watchdog.last_error or "")
    redis.fail_ping = False
    redis.fail_connect = False
    await watchdog.check_once()
    assert watchdog.redis_ok and runtime.ready


async def test_commander_not_running_and_reconnect_failure() -> None:
    watchdog, runtime, _, commander = make_watchdog()
    await watchdog.check_once()
    assert runtime.ready
    commander.running = False
    commander.fail_connect = True
    await watchdog.check_once()
    assert not watchdog.commander_ok
    assert not runtime.ready
    assert "Commander" in (watchdog.last_error or "")
    commander.running = True
    commander.fail_connect = False
    await watchdog.check_once()
    assert runtime.ready


async def test_revision_change_triggers_mapping_revalidation() -> None:
    watchdog, runtime, _, commander = make_watchdog()
    await watchdog.check_once()
    assert runtime.ready
    # 配置版本变化且测点类型失配：映射重校验失败，立即闭锁。
    commander.revision = "rev-b"
    commander.types["p"] = "int16"
    await watchdog.check_once()
    assert not watchdog.point_mappings_ok
    assert not runtime.ready
    assert "Commander/mappings" in (watchdog.last_error or "")
    commander.types["p"] = "float32"
    await watchdog.check_once()
    assert runtime.ready
    assert watchdog._revision == "rev-b"


async def test_missing_device_fails_mapping_verification() -> None:
    watchdog, runtime, _, commander = make_watchdog()
    commander.devices_set = set()
    await watchdog.check_once()
    assert not watchdog.point_mappings_ok
    assert not runtime.ready
    assert "Commander/mappings" in (watchdog.last_error or "")


async def test_start_stop_lifecycle() -> None:
    watchdog, runtime, _, _ = make_watchdog(interval=0.01)
    await watchdog.start()
    first = watchdog._task
    assert first is not None
    await watchdog.start()  # 幂等：不重复建任务
    assert watchdog._task is first
    assert runtime.ready
    await asyncio.sleep(0.05)
    await watchdog.stop()
    assert watchdog._task is None
    await watchdog.stop()  # 重复停止安全
