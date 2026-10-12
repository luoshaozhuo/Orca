"""使用隔离的 Redis 实例和本地 Commander gRPC 服务验证控制链。"""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest
import redis.asyncio as redis

from control.cycle import ControlCycle
from core.domain.config import RedisConfig
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.config.yaml import load_config
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.wind_hub.rpc import commander_pb2 as pb
from infrastructure.wind_hub.rpc import commander_pb2_grpc as pb_grpc

CONFIG = Path(__file__).resolve().parents[2] / "configs" / "template"
PAIR = ("wtg001", "active_power")


class SimulatedStrategy:
    def __init__(self) -> None:
        self.calls: list[dict[str, dict[str, float]]] = []

    def solve(self, measurements: dict[str, dict[str, float]]) -> ControlDecision:
        self.calls.append(measurements)
        return ControlDecision((Setpoint("wtg001", "active_power_setpoint", 2.0),))


class SimulatedCommander(pb_grpc.CommanderServiceServicer):
    def __init__(self) -> None:
        self.writes: list[pb.WritePointRequest] = []
        self.outcome = pb.COMMAND_OUTCOME_CONFIRMED_SUCCESS
        self.revision = "revision-a"

    async def GetStatus(self, request, context):
        return pb.CommanderStatusResponse(running=True, active_config_hash=self.revision)

    async def ListDevices(self, request, context):
        return pb.ListDevicesResponse(devices=[pb.DeviceSummary(device_id="wtg001", enabled=True)])

    async def ResolvePoint(self, request, context):
        if request.device_id != "wtg001" or request.point_id not in {
            "active_power", "active_power_setpoint"
        }:
            return pb.PointVerifyResponse(ok=False, error="unknown point")
        return pb.PointVerifyResponse(
            ok=True, device_id=request.device_id,
            point_id=request.point_id, data_type="float32",
        )

    async def WritePoint(self, request, context):
        self.writes.append(request)
        return pb.CommandResultMessage(command_id=request.command_id, outcome=self.outcome)


@pytest.fixture
async def redis_instance():
    """使用临时独立 Redis 进程，不触及默认 Redis 数据库。"""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server executable is required for real-service integration")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [executable, "--bind", "127.0.0.1", "--port", str(port),
         "--save", "", "--appendonly", "no", "--protected-mode", "yes"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("test Redis terminated unexpectedly")
            try:
                await client.ping()
                break
            except redis.ConnectionError:
                await asyncio.sleep(0.02)
        else:
            raise RuntimeError("test Redis did not become ready")
        yield port, client
    finally:
        await client.aclose()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if process.stderr is not None:
            process.stderr.close()


@pytest.fixture
async def commander_server():
    servicer = SimulatedCommander()
    server = grpc.aio.server()
    pb_grpc.add_CommanderServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    await server.start()
    try:
        yield f"127.0.0.1:{port}", servicer
    finally:
        await server.stop(grace=0)


async def publish(client: redis.Redis, *, value: float = 1.5,
                  quality: str = "good", age: float = 0.0) -> None:
    record = {
        "value": value,
        "quality": quality,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
        "source": "simulated-collector",
    }
    await client.set(RedisSource.key("windhub-test", *PAIR), json.dumps(record))


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
