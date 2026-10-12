"""本地 Commander gRPC 模拟服务；使用 Orca 已有的正式 Protobuf。"""
from __future__ import annotations

from dataclasses import dataclass, field

import grpc

from infrastructure.wind_hub.rpc import commander_pb2 as pb
from infrastructure.wind_hub.rpc import commander_pb2_grpc as pb_grpc


@dataclass
class CommanderScenario:
    revision: str = "revision-a"
    running: bool = True
    enabled_devices: set[str] = field(default_factory=lambda: {"wtg001"})
    points: dict[tuple[str, str], str] = field(default_factory=lambda: {
        ("wtg001", "active_power"): "float32",
        ("wtg001", "active_power_setpoint"): "float32",
    })
    outcome: int = pb.COMMAND_OUTCOME_CONFIRMED_SUCCESS
    writes: list[pb.WritePointRequest] = field(default_factory=list)


class SimulatedCommander(pb_grpc.CommanderServiceServicer):
    def __init__(self, scenario: CommanderScenario) -> None:
        self.scenario = scenario

    async def GetStatus(self, request, context):
        return pb.CommanderStatusResponse(
            running=self.scenario.running, active_config_hash=self.scenario.revision,
        )

    async def ListDevices(self, request, context):
        return pb.ListDevicesResponse(devices=[
            pb.DeviceSummary(device_id=device, enabled=True)
            for device in sorted(self.scenario.enabled_devices)
        ])

    async def ResolvePoint(self, request, context):
        data_type = self.scenario.points.get((request.device_id, request.point_id))
        if data_type is None:
            return pb.PointVerifyResponse(ok=False, error="unknown point")
        return pb.PointVerifyResponse(
            ok=True, device_id=request.device_id,
            point_id=request.point_id, data_type=data_type,
        )

    async def WritePoint(self, request, context):
        self.scenario.writes.append(request)
        return pb.CommandResultMessage(
            command_id=request.command_id, outcome=self.scenario.outcome,
        )


async def start_commander(
    scenario: CommanderScenario,
) -> tuple[grpc.aio.Server, str]:
    server = grpc.aio.server()
    pb_grpc.add_CommanderServiceServicer_to_server(
        SimulatedCommander(scenario), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    if not port:
        raise RuntimeError("failed to allocate simulated Commander port")
    await server.start()
    return server, f"127.0.0.1:{port}"
