"""Collector 元数据：生产 CollectorClient ↔ Mock Collector gRPC Server。"""
from __future__ import annotations

import grpc
import pytest

from infrastructure.wind_hub.collector import CollectorClient
from tests.support.collector import CollectorPoint, CollectorScenario

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.mock_service,
]


async def test_info_devices_and_revision(collector_server) -> None:
    target, scenario = await collector_server()
    client = CollectorClient(target, timeout=1.0)
    try:
        assert await client.info() == (True, "revision-a")
        assert await client.devices() == {"wtg001"}
        scenario.revision = "revision-b"
        assert await client.info() == (True, "revision-b")
    finally:
        await client.close()


async def test_points_metadata_found_and_missing(collector_server) -> None:
    scenario = CollectorScenario(
        devices={"wtg001"},
        points={
            ("wtg001", "active_power"): CollectorPoint(data_type="float32", access="ro"),
            ("wtg001", "wind_speed"): CollectorPoint(data_type="float64", access="ro"),
        },
    )
    target, _ = await collector_server(scenario)
    client = CollectorClient(target, timeout=1.0)
    try:
        metadata = await client.points_metadata(
            "wtg001", ["active_power", "wind_speed", "no_such_point"]
        )
        assert metadata["active_power"].found
        assert metadata["active_power"].data_type == "float32"
        assert metadata["active_power"].access == "ro"
        assert metadata["wind_speed"].data_type == "float64"
        assert not metadata["no_such_point"].found
        assert metadata["no_such_point"].error
        # 设备不存在：所有测点 unfound。
        missing = await client.points_metadata("no_such_device", ["active_power"])
        assert not missing["active_power"].found
    finally:
        await client.close()


async def test_unavailable_then_recovers(collector_server) -> None:
    target, scenario = await collector_server()
    client = CollectorClient(target, timeout=1.0)
    try:
        assert (await client.info())[0]
        scenario.unavailable = True
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await client.info()
        assert err.value.code() == grpc.StatusCode.UNAVAILABLE
        scenario.unavailable = False
        assert (await client.info())[0]
    finally:
        await client.close()


async def test_rpc_timeout_is_bounded(collector_server) -> None:
    target, scenario = await collector_server()
    scenario.delay = 2.0
    client = CollectorClient(target, timeout=0.2)
    try:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await client.info()
        assert err.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    finally:
        await client.close()
