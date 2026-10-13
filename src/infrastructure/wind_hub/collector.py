"""Collector gRPC 只读客户端：运行状态、设备与测点元数据；不触碰控制面。"""
from __future__ import annotations

from dataclasses import dataclass

import grpc
from google.protobuf.empty_pb2 import Empty

from .rpc import collector_pb2 as pb
from .rpc import collector_pb2_grpc as pb_grpc


@dataclass(frozen=True)
class PointMetadata:
    """单个输入测点的元数据；found=False 时 error 给出原因。"""

    found: bool
    data_type: str = ""
    access: str = ""
    error: str = ""


class CollectorClient:
    """仅使用 CollectorRuntimeService 的只读方法，绝不调用控制面 RPC。"""

    def __init__(self, target: str, timeout: float) -> None:
        self._target = target
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None
        self._stub: pb_grpc.CollectorRuntimeServiceStub | None = None

    async def connect(self) -> None:
        await self.close()
        self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = pb_grpc.CollectorRuntimeServiceStub(self._channel)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None

    async def info(self) -> tuple[bool, str]:
        """返回 (runtime_running, active_revision)。"""
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.GetCollectorInfo(Empty(), timeout=self._timeout)
        return bool(response.runtime_running), response.active_revision

    async def devices(self) -> set[str]:
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.ListDevices(Empty(), timeout=self._timeout)
        return {device.device_id for device in response.items}

    async def points_metadata(
        self, device_id: str, point_ids: list[str]
    ) -> dict[str, PointMetadata]:
        """查询输入测点元数据；缺失测点以 found=False 返回，不抛异常。"""
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.GetPointsMetadata(
            pb.PointsMetadataRequest(device_id=device_id, point_ids=point_ids),
            timeout=self._timeout,
        )
        return {
            result.point_id: PointMetadata(
                found=result.found,
                data_type=result.metadata.data_type,
                access=result.metadata.access,
                error=result.error,
            )
            for result in response.points
        }
