"""启动用例的离线契约测试，不连接现场设备。"""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from control.runtime import ControlRuntime
from core.application.validation import validate_inputs
from infrastructure.config.yaml import load_config
from infrastructure.wind_hub.watchdog import WindHubWatchdog

CONFIG = Path(__file__).resolve().parents[2] / "configs"


class FakeRedis:
    def __init__(self) -> None:
        self.connected = True

    async def ping(self) -> None:
        if not self.connected:
            raise ConnectionError("offline")

    async def connect(self) -> None:
        if not self.connected:
            raise ConnectionError("offline")

    async def close(self) -> None:
        pass

    async def latest(self, pairs):
        return {}


class FakeCommander:
    def __init__(self) -> None:
        self.online = True
        self.revision = "a"

    async def status(self):
        if not self.online:
            raise ConnectionError("offline")
        return True, self.revision

    async def devices(self):
        return {"wtg001"}

    async def resolve_point(self, device_id, point_id):
        return "float32"

    async def connect(self):
        if not self.online:
            raise ConnectionError("offline")

    async def close(self):
        pass


def test_load_config() -> None:
    config = load_config(CONFIG)
    assert config.site.site_id == "my_site"
    assert "wtg001" in config.resources
    assert config.wind_hub.collector.target == "127.0.0.1:50051"


def test_redis_key_contract() -> None:
    from infrastructure.redis.source import RedisSource
    assert RedisSource.key("windhub", "wtg001", "active_power") == "windhub:6:wtg001:12:active_power"


def test_stale_rejects_old_timestamp() -> None:
    config = load_config(CONFIG)
    old = datetime.now(UTC) - timedelta(seconds=20)
    records = {("wtg001", "active_power"): {
        "value": 1.0, "quality": "good", "timestamp": old.isoformat()
    }}
    assert "stale" in validate_inputs(config, "active_power", records)[0]


@pytest.mark.asyncio
async def test_watchdog_health_and_revalidation() -> None:
    config = load_config(CONFIG)
    redis = FakeRedis()
    commander = FakeCommander()
    runtime = ControlRuntime(config, redis)
    watchdog = WindHubWatchdog(config, runtime, redis, commander)
    await watchdog.check_once()
    assert runtime.ready
    redis.connected = False
    await watchdog.check_once()
    assert not runtime.ready
    redis.connected = True
    commander.revision = "b"
    await watchdog.check_once()
    assert runtime.ready


@pytest.mark.asyncio
async def test_task_requires_ready_and_stops() -> None:
    config = load_config(CONFIG)
    runtime = ControlRuntime(config, FakeRedis())
    with pytest.raises(RuntimeError):
        await runtime.start_task("active_power")
    runtime.ready = True
    await runtime.start_task("active_power")
    assert runtime.states["active_power"] == "RUNNING"
    await runtime.stop()
    assert runtime.states["active_power"] == "STOPPED"
