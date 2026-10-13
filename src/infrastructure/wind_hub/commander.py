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
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.ListDevices(Empty(), timeout=self._timeout)
        return {device.device_id for device in response.devices if device.enabled}

    async def resolve_point(self, device_id: str, point_id: str) -> str:
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        response = await self._stub.ResolvePoint(
            pb.PointRequest(device_id=device_id, point_id=point_id),
            timeout=self._timeout,
        )
        if not response.ok:
            raise ValueError(f"{device_id}/{point_id}: {response.error or response.code}")
        return response.data_type

    async def write_once(
        self, command_id: str, device_id: str, point_id: str, value: float
    ) -> str:
        """单次 gRPC WritePoint；UNKNOWN 不重试、不自动重连后重发。"""
        if self._stub is None:
            raise ConnectionError("Commander is not connected")
        request = pb.WritePointRequest(
            command_id=command_id,
            device_id=device_id,
            point_id=point_id,
            value=pb.ScalarValue(double_value=value),
            timeout=self._timeout,
        )
        result = await self._stub.WritePoint(
            request, timeout=self._timeout, wait_for_ready=False
        )
        if result.command_id != command_id:
            raise ValueError("Commander command_id mismatch: outcome unknown")
        outcomes = {
            pb.COMMAND_OUTCOME_CONFIRMED_SUCCESS: "CONFIRMED_SUCCESS",
            pb.COMMAND_OUTCOME_CONFIRMED_FAILURE: "CONFIRMED_FAILURE",
            pb.COMMAND_OUTCOME_UNKNOWN: "UNKNOWN",
        }
        return outcomes.get(result.outcome, "UNKNOWN")
