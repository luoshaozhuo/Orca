"""Commander 元数据与写入：生产 CommanderClient ↔ Mock Commander gRPC Server。

所有控制指令只发送给 Mock Commander，不触及真实设备。
"""
from __future__ import annotations

import grpc
import pytest

from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.wind_hub.rpc import commander_pb2 as pb
from tests.support.commander import CommanderPoint, CommanderScenario

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.mock_service,
]


async def test_status_devices_and_revision(commander_server) -> None:
    target, scenario = await commander_server()
    client = CommanderClient(target, timeout=1.0)
    try:
        assert await client.status() == (True, "revision-a")
        assert await client.devices() == {"wtg001"}
        scenario.revision = "revision-b"
        assert (await client.status())[1] == "revision-b"
    finally:
        await client.close()


async def test_resolve_point_type_and_unknown(commander_server) -> None:
    scenario = CommanderScenario(
        points={("wtg001", "active_power_setpoint"): CommanderPoint(data_type="float32")},
    )
    target, _ = await commander_server(scenario)
    client = CommanderClient(target, timeout=1.0)
    try:
        assert await client.resolve_point("wtg001", "active_power_setpoint") == "float32"
        with pytest.raises(ValueError, match="unknown point"):
            await client.resolve_point("wtg001", "no_such_point")
        with pytest.raises(ValueError):
            await client.resolve_point("no_such_device", "active_power_setpoint")
    finally:
        await client.close()


async def test_write_confirmed_success_records_command(commander_server) -> None:
    target, scenario = await commander_server()
    client = CommanderClient(target, timeout=1.0)
    try:
        await client.connect()
        outcome = await client.write_once("cmd-1", "wtg001", "active_power_setpoint", 2.0)
        assert outcome == "CONFIRMED_SUCCESS"
        assert len(scenario.writes) == 1
        written = scenario.writes[0]
        assert written.command_id == "cmd-1"
        assert written.device_id == "wtg001"
        assert written.point_id == "active_power_setpoint"
        assert written.value.double_value == 2.0
    finally:
        await client.close()


async def test_write_confirmed_failure(commander_server) -> None:
    target, _ = await commander_server(
        CommanderScenario(outcome=pb.COMMAND_OUTCOME_CONFIRMED_FAILURE)
    )
    client = CommanderClient(target, timeout=1.0)
    try:
        await client.connect()
        assert await client.write_once("cmd-2", "wtg001", "active_power_setpoint", 2.0) \
            == "CONFIRMED_FAILURE"
    finally:
        await client.close()


async def test_write_unknown_not_retried(commander_server) -> None:
    target, scenario = await commander_server(
        CommanderScenario(outcome=pb.COMMAND_OUTCOME_UNKNOWN)
    )
    client = CommanderClient(target, timeout=1.0)
    try:
        await client.connect()
        assert await client.write_once("cmd-3", "wtg001", "active_power_setpoint", 2.0) \
            == "UNKNOWN"
        # UNKNOWN：命令可能已送达，客户端不得重传。
        assert len(scenario.writes) == 1
    finally:
        await client.close()


async def test_response_lost_not_retried(commander_server) -> None:
    target, scenario = await commander_server(CommanderScenario(response_lost=True))
    client = CommanderClient(target, timeout=1.0)
    try:
        await client.connect()
        with pytest.raises(grpc.aio.AioRpcError):
            await client.write_once("cmd-4", "wtg001", "active_power_setpoint", 2.0)
        # 响应丢失：写入可能已执行，客户端报错但不盲目重试。
        assert len(scenario.writes) == 1
    finally:
        await client.close()


async def test_write_timeout_is_unknown_and_not_retried(commander_server) -> None:
    target, scenario = await commander_server(CommanderScenario(write_delay=2.0))
    client = CommanderClient(target, timeout=0.2)
    try:
        await client.connect()
        with pytest.raises(grpc.aio.AioRpcError):
            await client.write_once("cmd-5", "wtg001", "active_power_setpoint", 2.0)
        assert len({write.command_id for write in scenario.writes}) == 1
    finally:
        await client.close()


async def test_unavailable_then_recovers(commander_server) -> None:
    target, scenario = await commander_server()
    client = CommanderClient(target, timeout=1.0)
    try:
        assert (await client.status())[0]
        scenario.unavailable = True
        with pytest.raises(grpc.aio.AioRpcError):
            await client.status()
        scenario.unavailable = False
        assert (await client.status())[0]
    finally:
        await client.close()
