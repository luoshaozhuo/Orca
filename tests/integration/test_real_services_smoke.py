"""显式授权后才连接真实 Collector/Commander；绝不执行 WritePoint。"""
from __future__ import annotations

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from infrastructure.wind_hub.commander import CommanderClient
from tests.support.collector import CollectorInfo
from tests.support.endpoints import select_endpoints


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_real_collector_commander_read_only():
    import os

    if os.environ.get("ORCA_TEST_ALLOW_REAL_SERVICES") != "yes":
        pytest.skip("set ORCA_TEST_ALLOW_REAL_SERVICES=yes for explicit real-service testing")
    endpoints = select_endpoints("real")
    assert endpoints.collector is not None
    assert endpoints.commander is not None
    channel = grpc.aio.insecure_channel(endpoints.collector)
    commander = CommanderClient(endpoints.commander, timeout=2.0)
    try:
        rpc = channel.unary_unary(
            "/windhub.collector.v1.CollectorRuntimeService/GetCollectorInfo",
            request_serializer=Empty.SerializeToString,
            response_deserializer=CollectorInfo.FromString,
        )
        info = await rpc(Empty(), timeout=2.0)
        assert info.component == "collector"
        await commander.connect()
        running, revision = await commander.status()
        assert isinstance(running, bool)
        assert isinstance(revision, str)
        devices = await commander.devices()
        assert isinstance(devices, set)
        # 元数据契约尚未在已确认的 wind-hub main 中公开，不推断可写权限。
    finally:
        await commander.close()
        await channel.close()
