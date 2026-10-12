"""Collector 只读模拟服务：仅实现当前 Protobuf 已公开的控制面方法。"""
from __future__ import annotations

from dataclasses import dataclass

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def _collector_info_type():
    file = descriptor_pb2.FileDescriptorProto()
    file.name = "orca_test_collector_info.proto"
    file.package = "windhub.collector.v1"
    message = file.message_type.add()
    message.name = "CollectorInfoResponse"
    for number, name, kind in (
        (1, "component", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
        (2, "collector_id", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
        (4, "config_hash", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
        (9, "active_revision", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
        (11, "runtime_running", descriptor_pb2.FieldDescriptorProto.TYPE_BOOL),
    ):
        field = message.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = kind
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return message_factory.GetMessageClass(
        pool.FindMessageTypeByName("windhub.collector.v1.CollectorInfoResponse")
    )


CollectorInfo = _collector_info_type()


@dataclass
class CollectorScenario:
    revision: str = "revision-a"
    running: bool = True


async def start_collector(scenario: CollectorScenario) -> tuple[grpc.aio.Server, str]:
    async def info(request, context):
        response = CollectorInfo(
            component="collector",
            collector_id="orca-test",
            config_hash=scenario.revision,
            active_revision=scenario.revision,
            runtime_running=scenario.running,
        )
        return response.SerializeToString()

    service = grpc.method_handlers_generic_handler(
        "windhub.collector.v1.CollectorRuntimeService",
        {"GetCollectorInfo": grpc.unary_unary_rpc_method_handler(
            info,
            request_deserializer=lambda payload: payload,
            response_serializer=lambda payload: payload,
        )},
    )
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((service,))
    port = server.add_insecure_port("127.0.0.1:0")
    if not port:
        raise RuntimeError("failed to allocate simulated Collector port")
    await server.start()
    return server, f"127.0.0.1:{port}"
