"""Mock Commander gRPC Server：使用 Orca 已有的正式 Commander Protobuf。

仅模拟远端服务端；Orca 生产 CommanderClient 经真实 gRPC 连接。
接收并记录 WritePoint，绝不连接现场设备。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import grpc
from google.protobuf import wrappers_pb2

from infrastructure.wind_hub.rpc import commander_pb2 as pb
from infrastructure.wind_hub.rpc import commander_pb2_grpc as pb_grpc


@dataclass
class CommanderPoint:
    data_type: str = "float32"
    writable: bool = True


@dataclass
class CommanderScenario:
    """单个测试独占的场景状态，不在测试间共享。"""

    revision: str = "revision-a"
    running: bool = True
    enabled_devices: set[str] = field(default_factory=lambda: {"wtg001"})
    points: dict[tuple[str, str], CommanderPoint] = field(default_factory=lambda: {
        ("wtg001", "active_power"): CommanderPoint(writable=False),
        ("wtg001", "active_power_setpoint"): CommanderPoint(),
    })
    outcome: int = pb.COMMAND_OUTCOME_CONFIRMED_SUCCESS
    writes: list[pb.WritePointRequest] = field(default_factory=list)
    # 故障注入：unavailable 时所有 RPC 失败；response_lost 时写入已记录
    # 但响应以 UNAVAILABLE 中断（客户端无法确认执行结果）；write_delay 触发超时。
    unavailable: bool = False
    response_lost: bool = False
    write_delay: float = 0.0


class SimulatedCommander(pb_grpc.CommanderServiceServicer):
    def __init__(self, scenario: CommanderScenario) -> None:
        self.scenario = scenario

    async def _reject_if_unavailable(self, context) -> None:
        if self.scenario.unavailable:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "commander unavailable (injected)")

    async def GetStatus(self, request, context):
        await self._reject_if_unavailable(context)
        return pb.CommanderStatusResponse(
            running=self.scenario.running,
            active_revision=self.scenario.revision,
            active_config_hash=self.scenario.revision,
        )

    async def ListDevices(self, request, context):
        await self._reject_if_unavailable(context)
        return pb.ListDevicesResponse(devices=[
            pb.DeviceSummary(device_id=device, enabled=True)
            for device in sorted(self.scenario.enabled_devices)
        ])

    async def ResolvePoint(self, request, context):
        await self._reject_if_unavailable(context)
        point = self.scenario.points.get((request.device_id, request.point_id))
        if point is None:
            return pb.PointVerifyResponse(ok=False, error="unknown point")
        return pb.PointVerifyResponse(
            ok=True,
            device_id=request.device_id,
            point_id=request.point_id,
            data_type=point.data_type,
            readable=wrappers_pb2.BoolValue(value=True),
        )

    async def WritePoint(self, request, context):
        await self._reject_if_unavailable(context)
        self.scenario.writes.append(request)
        if self.scenario.write_delay > 0:
            await asyncio.sleep(self.scenario.write_delay)
        if self.scenario.response_lost:
            # 写入已记录但响应丢失：客户端必须按 UNKNOWN 处理，不得重试。
            await context.abort(grpc.StatusCode.UNAVAILABLE, "response lost (injected)")
        return pb.CommandResultMessage(
            command_id=request.command_id, outcome=self.scenario.outcome,
        )


async def start_commander(scenario: CommanderScenario) -> tuple[grpc.aio.Server, str]:
    server = grpc.aio.server()
    pb_grpc.add_CommanderServiceServicer_to_server(
        SimulatedCommander(scenario), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    if not port:
        raise RuntimeError("failed to allocate simulated Commander port")
    await server.start()
    return server, f"127.0.0.1:{port}"
