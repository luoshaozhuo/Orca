"""协议级恢复与批量执行语义：真实 gRPC 服务端停止/重启、批量部分成功、在途取消。

安全边界：UNKNOWN 绝不自动重传；批量写入中任一命令失败后不再提交后续命令；
在途写入被取消后绝不补发。所有写入只到达 Mock Commander。
"""
from __future__ import annotations

import asyncio
import time

import grpc
import pytest

from core.application.joint_power_allocator import JointAllocationResult
from core.application.read_only_planning_cycle import PlanningCycleResult
from core.application.safe_plan_dispatcher import SafePlanDispatcher
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.safe_dispatch import DispatchStatus, OutputBinding
from infrastructure.wind_hub.collector import CollectorClient
from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.wind_hub.rpc import collector_pb2_grpc as collector_grpc
from infrastructure.wind_hub.rpc import commander_pb2 as pb
from infrastructure.wind_hub.rpc import commander_pb2_grpc as commander_grpc
from tests.support.collector import (
    CollectorScenario,
    SimulatedCollector,
    start_collector,
)
from tests.support.commander import (
    CommanderPoint,
    CommanderScenario,
    SimulatedCommander,
    start_commander,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.reliability,
    pytest.mark.mock_service,
]

TASK = "pq"
WTG = "test-wtg001"
WTG2 = "test-wtg002"
GENERATION = 7


def setpoint(device: str, quantity: ControlQuantity, value: float) -> ControlValue:
    unit = "Mvar" if quantity == ControlQuantity.REACTIVE_POWER else "MW"
    return ControlValue(
        quantity, ControlScope.DEVICE, device,
        ControlValueRole.DEVICE_SETPOINT, ControlValueKind.ABSOLUTE, value, unit,
    )


def plan(
    active: tuple[ControlValue, ...] = (setpoint(WTG, ControlQuantity.ACTIVE_POWER, 6.0),),
    reactive: tuple[ControlValue, ...] = (),
) -> PlanningCycleResult:
    return PlanningCycleResult(
        allocation=JointAllocationResult(
            active=active, reactive=reactive,
            p_residual=0.0, q_residual=0.0,
            generation=GENERATION, feasible=True,
        ),
        acquired_at_ns=time.time_ns(),
    )


def make_dispatcher(
    commander: CommanderClient,
    bindings: tuple[OutputBinding, ...],
) -> SafePlanDispatcher:
    return SafePlanDispatcher(
        commander,
        bindings,
        maximum_age_ns=5_000_000_000,
        permitted=lambda task_id: True,
        generation=lambda: GENERATION,
        output_owner=lambda device, point: TASK,
    )


async def start_commander_on(scenario: CommanderScenario, port: int) -> grpc.aio.Server:
    """在指定端口重建 Mock Commander（模拟进程重启，场景状态保留）。"""
    server = grpc.aio.server()
    commander_grpc.add_CommanderServiceServicer_to_server(
        SimulatedCommander(scenario), server,
    )
    if server.add_insecure_port(f"127.0.0.1:{port}") != port:
        raise RuntimeError("failed to rebind simulated Commander port")
    await server.start()
    return server


async def start_collector_on(scenario: CollectorScenario, port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    collector_grpc.add_CollectorRuntimeServiceServicer_to_server(
        SimulatedCollector(scenario), server,
    )
    if server.add_insecure_port(f"127.0.0.1:{port}") != port:
        raise RuntimeError("failed to rebind simulated Collector port")
    await server.start()
    return server


def single_binding() -> tuple[OutputBinding, ...]:
    return (
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
        ),
    )


async def test_commander_restart_recovers_without_retransmit() -> None:
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={(WTG, "active_power_setpoint"): CommanderPoint()},
    )
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    dispatcher = make_dispatcher(commander, single_binding())
    port = int(target.rsplit(":", 1)[1])
    try:
        first = await dispatcher.dispatch(TASK, plan())
        assert first.status == DispatchStatus.CONFIRMED_SUCCESS
        assert len(scenario.writes) == 1

        # 服务端进程真实停止：写入有界失败为 UNKNOWN，且没有任何报文到达。
        await server.stop(grace=0)
        outage = await dispatcher.dispatch(TASK, plan())
        assert outage.status == DispatchStatus.UNKNOWN
        assert len(scenario.writes) == 1

        # 同端口重启后恢复；停机期间的 UNKNOWN 从未被补发，恢复后是新指令。
        server = await start_commander_on(scenario, port)
        recovered = None
        for _ in range(50):
            recovered = await dispatcher.dispatch(TASK, plan())
            if recovered.status == DispatchStatus.CONFIRMED_SUCCESS:
                break
            await asyncio.sleep(0.1)
        assert recovered is not None and recovered.status == DispatchStatus.CONFIRMED_SUCCESS
        assert len(scenario.writes) >= 2
        command_ids = [write.command_id for write in scenario.writes]
        assert len(command_ids) == len(set(command_ids))
    finally:
        await commander.close()
        await server.stop(grace=0)


async def test_partial_success_never_submits_remaining_commands() -> None:
    """批量第 2 条失败：第 3 条绝不提交，已确认条数如实上报，不补发。"""
    scenario = CommanderScenario(
        enabled_devices={WTG, WTG2},
        points={
            (WTG, "active_power_setpoint"): CommanderPoint(),
            (WTG, "reactive_power_setpoint"): CommanderPoint(),
            (WTG2, "active_power_setpoint"): CommanderPoint(),
        },
    )

    class FailAfterFirstWrite(list):
        def append(self, item: object) -> None:
            super().append(item)
            if len(self) == 2:
                scenario.outcome = pb.COMMAND_OUTCOME_CONFIRMED_FAILURE

    scenario.writes = FailAfterFirstWrite()
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 2.0)
    await commander.connect()
    bindings = (
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
        ),
        OutputBinding(
            device_id=WTG2, quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint", minimum=0.0, maximum=10.0,
        ),
        OutputBinding(
            device_id=WTG, quantity=ControlQuantity.REACTIVE_POWER,
            point_id="reactive_power_setpoint", minimum=-5.0, maximum=5.0,
        ),
    )
    dispatcher = make_dispatcher(commander, bindings)
    batch = plan(
        active=(
            setpoint(WTG, ControlQuantity.ACTIVE_POWER, 6.0),
            setpoint(WTG2, ControlQuantity.ACTIVE_POWER, 4.0),
        ),
        reactive=(setpoint(WTG, ControlQuantity.REACTIVE_POWER, 1.0),),
    )
    try:
        receipt = await dispatcher.dispatch(TASK, batch)
        assert receipt.status == DispatchStatus.CONFIRMED_FAILURE
        assert receipt.confirmed == 1
        # 失败的第 2 条到达过服务端；第 3 条从未提交。
        assert len(scenario.writes) == 2
        assert len(receipt.command_ids) == 2
        submitted_points = [(w.device_id, w.point_id) for w in scenario.writes]
        assert (WTG, "reactive_power_setpoint") not in submitted_points
    finally:
        await commander.close()
        await server.stop(grace=0)


async def test_in_flight_dispatch_cancellation_is_not_retransmitted() -> None:
    """在途写入随任务停止被取消：结果按 UNKNOWN 处理，绝不补发。"""
    scenario = CommanderScenario(
        enabled_devices={WTG},
        points={(WTG, "active_power_setpoint"): CommanderPoint()},
        write_delay=30.0,
    )
    server, target = await start_commander(scenario)
    commander = CommanderClient(target, 60.0)
    await commander.connect()
    dispatcher = make_dispatcher(commander, single_binding())
    try:
        job = asyncio.create_task(dispatcher.dispatch(TASK, plan()))
        await asyncio.sleep(0.2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        # 取消后等待服务端残余处理：写入至多到达一次，绝无自动重传。
        await asyncio.sleep(0.2)
        assert len(scenario.writes) <= 1
        in_flight_ids = [write.command_id for write in scenario.writes]

        # 恢复后的显式 dispatch 是全新指令，不复用在途 command_id。
        scenario.write_delay = 0.0
        receipt = await dispatcher.dispatch(TASK, plan())
        assert receipt.status == DispatchStatus.CONFIRMED_SUCCESS
        assert scenario.writes[-1].command_id not in in_flight_ids
    finally:
        await commander.close()
        await server.stop(grace=0)


async def test_collector_restart_recovers_and_revision_change_is_visible() -> None:
    scenario = CollectorScenario()
    server, target = await start_collector(scenario)
    client = CollectorClient(target, 1.0)
    await client.connect()
    port = int(target.rsplit(":", 1)[1])
    try:
        running, revision = await client.info()
        assert running and revision == "revision-a"

        await server.stop(grace=0)
        with pytest.raises(grpc.aio.AioRpcError):
            await client.info()

        # 重启后配置版本变化必须对客户端可见（看门狗据此触发元数据重校验）。
        scenario.revision = "revision-b"
        server = await start_collector_on(scenario, port)
        recovered: tuple[bool, str] | None = None
        for _ in range(50):
            try:
                recovered = await client.info()
                break
            except grpc.aio.AioRpcError:
                await asyncio.sleep(0.1)
        assert recovered is not None
        assert recovered == (True, "revision-b")
    finally:
        await client.close()
        await server.stop(grace=0)
