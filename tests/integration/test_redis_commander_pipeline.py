"""使用隔离的 Redis 实例和本地 Commander gRPC 服务验证控制链。"""
from __future__ import annotations

from pathlib import Path

import grpc
import pytest
import pytest_asyncio
import redis.asyncio as redis

from control.cycle import ControlCycle
from control.runtime import ControlRuntime
from core.domain.config import RedisConfig
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.config.yaml import load_config
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.wind_hub.watchdog import WindHubWatchdog
from infrastructure.wind_hub.rpc import commander_pb2 as pb
from tests.support.collector import CollectorScenario, start_collector
from tests.support.docker_redis import docker_redis_port
from tests.support.redis_data import publish_measurement
from tests.support.services import CommanderScenario, start_commander

CONFIG = Path(__file__).resolve().parents[2] / "configs"
PAIR = ("wtg001", "active_power")


class SimulatedStrategy:
    def __init__(self) -> None:
        self.calls: list[dict[str, dict[str, float]]] = []

    def solve(self, measurements: dict[str, dict[str, float]]) -> ControlDecision:
        self.calls.append(measurements)
        return ControlDecision((Setpoint("wtg001", "active_power_setpoint", 2.0),))


@pytest_asyncio.fixture
async def redis_instance(docker_redis_port):
    client = redis.Redis(
        host="127.0.0.1", port=docker_redis_port, decode_responses=True,
    )
    try:
        assert await client.ping()
        yield docker_redis_port, client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def commander_server():
    scenario = CommanderScenario()
    server, target = await start_commander(scenario)
    try:
        yield target, scenario
    finally:
        await server.stop(grace=0)


async def publish(client, *, value=1.5, quality="good", age=0.0):
    await publish_measurement(
        client, *PAIR, value, prefix="windhub-test",
        quality=quality, age_seconds=age,
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
@pytest.mark.mock_service
async def test_redis_to_commander_and_input_guards(redis_instance, commander_server):
    port, raw_redis = redis_instance
    target, simulated = commander_server
    config = load_config(CONFIG)
    source = RedisSource(
        RedisConfig(host="127.0.0.1", port=port, key_prefix="windhub-test"), timeout=1.0
    )
    commander = CommanderClient(target, timeout=1.0)
    strategy = SimulatedStrategy()
    cycle = ControlCycle(config, source, commander, strategy)
    try:
        await source.connect()
        await commander.connect()
        assert await commander.status() == (True, "revision-a")
        assert await commander.devices() == {"wtg001"}
        assert await commander.resolve_point(*PAIR) == "float32"
        assert await commander.resolve_point("wtg001", "active_power_setpoint") == "float32"

        # 缺测、坏质量、过期均不能到达算法和 Commander。
        assert "missing" in (await cycle.execute("active_power", allow_write=True) or "")
        await publish(raw_redis, quality="bad")
        assert "bad quality" in (await cycle.execute("active_power", allow_write=True) or "")
        await publish(raw_redis, age=60)
        assert "stale" in (await cycle.execute("active_power", allow_write=True) or "")
        assert strategy.calls == []
        assert simulated.writes == []

        await publish(raw_redis, value=1.75)
        assert await cycle.execute("active_power", allow_write=False) is not None
        assert not simulated.writes

        assert await cycle.execute("active_power", allow_write=True) is None
        assert strategy.calls[-1] == {"wtg001": {"active_power": 1.75}}
        assert len(simulated.writes) == 1
        written = simulated.writes[0]
        assert written.device_id == "wtg001"
        assert written.point_id == "active_power_setpoint"
        assert written.value.double_value == 2.0
        assert written.command_id

        simulated.outcome = pb.COMMAND_OUTCOME_UNKNOWN
        result = await cycle.execute("active_power", allow_write=True)
        assert result is not None and "UNKNOWN" in result
        assert len(simulated.writes) == 2
        assert simulated.writes[0].command_id != simulated.writes[1].command_id
        # UNKNOWN 不重传历史命令。
        assert len({request.command_id for request in simulated.writes}) == 2
    finally:
        await commander.close()
        await source.close()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
@pytest.mark.mock_service
async def test_watchdog_revalidates_commander_revision(redis_instance, commander_server):
    port, _ = redis_instance
    target, simulated = commander_server
    config = load_config(CONFIG)
    source = RedisSource(
        RedisConfig(host="127.0.0.1", port=port, key_prefix="windhub-test"), timeout=1.0
    )
    commander = CommanderClient(target, timeout=1.0)
    runtime = ControlRuntime(config, source)
    watchdog = WindHubWatchdog(config, runtime, source, commander)
    try:
        await source.connect()
        await commander.connect()
        await watchdog.check_once()
        assert runtime.ready
        assert watchdog.point_mappings_ok
        assert not watchdog.output_access_verified
        simulated.revision = "revision-b"
        await watchdog.check_once()
        assert runtime.ready
        # 当前 Commander 协议尚无 access 字段，不能将解析成功视作输出可写。
        assert not watchdog.output_access_verified
        assert not runtime.write_enabled
    finally:
        await commander.close()
        await source.close()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.mock_service
async def test_simulated_collector_status_rpc():
    from google.protobuf.empty_pb2 import Empty

    scenario = CollectorScenario()
    server, target = await start_collector(scenario)
    channel = grpc.aio.insecure_channel(target)
    rpc = channel.unary_unary(
        "/windhub.collector.v1.CollectorRuntimeService/GetCollectorInfo",
        request_serializer=Empty.SerializeToString,
        response_deserializer=lambda raw: raw,
    )
    try:
        from tests.support.collector import CollectorInfo

        response = CollectorInfo.FromString(await rpc(Empty(), timeout=1.0))
        assert response.runtime_running
        assert response.active_revision == "revision-a"
        scenario.revision = "revision-b"
        changed = CollectorInfo.FromString(await rpc(Empty(), timeout=1.0))
        assert changed.active_revision == "revision-b"
    finally:
        await channel.close()
        await server.stop(grace=0)
