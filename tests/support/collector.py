"""Mock Collector gRPC Server：使用 wind-hub 正式 Protobuf 契约的只读控制面。

仅模拟远端服务端；Orca 生产 CollectorClient 经真实 gRPC 连接。
不连接实际 PLC，不负责控制指令下发。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import grpc

from infrastructure.wind_hub.rpc import collector_pb2 as pb
from infrastructure.wind_hub.rpc import collector_pb2_grpc as pb_grpc


@dataclass
class CollectorPoint:
    data_type: str = "float32"
    access: str = "ro"


@dataclass
class CollectorScenario:
    """单个测试独占的场景状态，不在测试间共享。"""

    revision: str = "revision-a"
    running: bool = True
    devices: set[str] = field(default_factory=lambda: {"wtg001"})
    points: dict[tuple[str, str], CollectorPoint] = field(default_factory=lambda: {
        ("wtg001", "active_power"): CollectorPoint(),
    })
    # 故障注入：unavailable 时所有 RPC 以 UNAVAILABLE 失败，可随时恢复；
    # delay 模拟服务端慢响应，用于客户端超时验证。
    unavailable: bool = False
    delay: float = 0.0


class SimulatedCollector(pb_grpc.CollectorRuntimeServiceServicer):
    def __init__(self, scenario: CollectorScenario) -> None:
        self.scenario = scenario

    async def _reject_if_unavailable(self, context) -> None:
        if self.scenario.unavailable:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "collector unavailable (injected)")

    async def GetCollectorInfo(self, request, context):
        await self._reject_if_unavailable(context)
        if self.scenario.delay > 0:
            await asyncio.sleep(self.scenario.delay)
        return pb.CollectorInfoResponse(
            component="collector",
            collector_id="orca-test",
            active_revision=self.scenario.revision,
            runtime_running=self.scenario.running,
        )

    async def ListDevices(self, request, context):
        await self._reject_if_unavailable(context)
        return pb.ListDevicesResponse(items=[
            pb.DeviceInfoMessage(device_id=device, connected=True)
            for device in sorted(self.scenario.devices)
        ])

    async def GetPointsMetadata(self, request, context):
        await self._reject_if_unavailable(context)
        results = []
        for point_id in request.point_ids:
            point = self.scenario.points.get((request.device_id, point_id))
            if point is None or request.device_id not in self.scenario.devices:
                results.append(pb.PointMetadataResult(
                    point_id=point_id, found=False, error="unknown device or point",
                ))
            else:
                results.append(pb.PointMetadataResult(
                    point_id=point_id,
                    found=True,
                    metadata=pb.PointMetadata(
                        point_id=point_id,
                        access=point.access,
                        data_type=point.data_type,
                    ),
                ))
        return pb.PointsMetadataResponse(
            device_id=request.device_id,
            active_revision=self.scenario.revision,
            points=results,
        )


async def start_collector(scenario: CollectorScenario) -> tuple[grpc.aio.Server, str]:
    server = grpc.aio.server()
    pb_grpc.add_CollectorRuntimeServiceServicer_to_server(
        SimulatedCollector(scenario), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    if not port:
        raise RuntimeError("failed to allocate simulated Collector port")
    await server.start()
    return server, f"127.0.0.1:{port}"
