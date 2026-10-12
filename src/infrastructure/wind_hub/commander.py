"""Commander gRPC 元数据检查；不执行设备写入。"""
from __future__ import annotations

import grpc
from google.protobuf.empty_pb2 import Empty

from .rpc import commander_pb2 as pb
from .rpc import commander_pb2_grpc as pb_grpc


class CommanderClient:
    """只使用 Commander 管理接口，绝不通过验证写入真实设备。"""

    def __init__(self, target: str, timeout: float) -> None:
        self._target = target
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None
        self._stub: pb_grpc.CommanderServiceStub | None = None

    async def connect(self) -> None:
        await self.close()
        self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = pb_grpc.CommanderServiceStub(self._channel)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None

    async def status(self) -> tuple[bool, str]:
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.GetStatus(Empty(), timeout=self._timeout)
        return bool(response.running), response.active_config_hash

    async def devices(self) -> set[str]:
        assert self._stub is not None
        response = await self._stub.ListDevices(Empty(), timeout=self._timeout)
        return {device.device_id for device in response.devices if device.enabled}

    async def resolve_point(self, device_id: str, point_id: str) -> str:
        assert self._stub is not None
        response = await self._stub.ResolvePoint(
            pb.PointRequest(device_id=device_id, point_id=point_id),
            timeout=self._timeout,
        )
        if not response.ok:
            raise ValueError(f"{device_id}/{point_id}: {response.error or response.code}")
        return response.data_type
