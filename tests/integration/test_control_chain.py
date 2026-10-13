"""控制链集成测试：真实 Redis + 生产 RedisSource/CommanderClient + Mock Commander。

配置由 configs/system.yaml 派生（测试命名空间 + 每测试唯一设备 ID），
控制指令只发送给 Mock Commander。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from control.cycle import ControlCycle
from control.runtime import ControlRuntime
from core.domain.decision import ControlDecision, Setpoint
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.wind_hub.rpc import commander_pb2 as pb
from infrastructure.wind_hub.watchdog import WindHubWatchdog
from tests.integration.conftest import derive_test_config
from tests.support.commander import CommanderPoint, CommanderScenario

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.real_service,
    pytest.mark.mock_service,
]

TASK = "active_power"


class SimulatedStrategy:
    def __init__(self, device: str) -> None:
        self.device = device
        self.calls: list[dict[str, dict[str, float]]] = []

    def solve(self, measurements: dict[str, dict[str, float]]) -> ControlDecision:
        self.calls.append(measurements)
        return ControlDecision((Setpoint(self.device, "active_power_setpoint", 2.0),))


def chain_scenario(device: str) -> CommanderScenario:
    return CommanderScenario(
        enabled_devices={device},
        points={
            (device, "active_power"): CommanderPoint(writable=False),
            (device, "active_power_setpoint"): CommanderPoint(),
        },
    )


async def assemble(orca_config, commander_server):
    """每测试唯一设备 ID：共享 Redis 上彻底隔离，不受残留 TTL 键影响。"""
    device = f"test-wtg-{uuid4().hex[:8]}"
    config = derive_test_config(orca_config, device)
    target, scenario = await commander_server(chain_scenario(device))
    source = RedisSource(config.wind_hub.redis, timeout=2.0)
    commander = CommanderClient(target, timeout=1.0)
    strategy = SimulatedStrategy(device)
    cycle = ControlCycle(config, source, commander, strategy)
    return config, device, source, commander, cycle, scenario, strategy


async def test_input_guards_and_write_path(orca_config, data_publisher, commander_server) -> None:
    _config, device, source, commander, cycle, scenario, strategy = \
        await assemble(orca_config, commander_server)
    pair = (device, "active_power")
    try:
        await source.connect()
        await commander.connect()
        assert await commander.status() == (True, "revision-a")
        assert await commander.devices() == {device}
        assert await commander.resolve_point(*pair) == "float32"

        # 缺测、坏质量、过期、非数值均不能到达算法和 Commander。
        assert "missing" in (await cycle.execute(TASK, allow_write=True) or "")
        await data_publisher.publish(*pair, 1.5, quality="bad")
        assert "bad quality" in (await cycle.execute(TASK, allow_write=True) or "")
        await data_publisher.publish(*pair, 1.5, age_seconds=60)
        assert "stale" in (await cycle.execute(TASK, allow_write=True) or "")
        await data_publisher.publish(*pair, "not-a-number")
        assert "numeric" in (await cycle.execute(TASK, allow_write=True) or "")
        assert strategy.calls == []
        assert scenario.writes == []

        # 未授权写入时不下发。
        await data_publisher.publish(*pair, 1.75)
        assert await cycle.execute(TASK, allow_write=False) is not None
        assert not scenario.writes

        # 授权后完整链路：算法求解 + 指令下发。
        assert await cycle.execute(TASK, allow_write=True) is None
        assert strategy.calls[-1] == {device: {"active_power": 1.75}}
        assert len(scenario.writes) == 1
        written = scenario.writes[0]
        assert written.device_id == device
        assert written.point_id == "active_power_setpoint"
        assert written.value.double_value == 2.0
        assert written.command_id

        # UNKNOWN 不重传历史命令，command_id 唯一。
        scenario.outcome = pb.COMMAND_OUTCOME_UNKNOWN
        result = await cycle.execute(TASK, allow_write=True)
        assert result is not None and "UNKNOWN" in result
        assert len(scenario.writes) == 2
        assert len({write.command_id for write in scenario.writes}) == 2
    finally:
        await commander.close()
        await source.close()


async def test_watchdog_health_and_revision_revalidation(orca_config, commander_server) -> None:
    config, _, source, commander, _, scenario, _ = \
        await assemble(orca_config, commander_server)
    runtime = ControlRuntime(config, source)
    watchdog = WindHubWatchdog(config, runtime, source, commander)
    try:
        await source.connect()
        await commander.connect()
        await watchdog.check_once()
        assert runtime.ready
        assert watchdog.point_mappings_ok
        # Commander 契约尚无 access 字段，不能将解析成功视作输出可写。
        assert not watchdog.output_access_verified
        assert not runtime.write_enabled

        # 依赖故障闭锁与恢复。
        scenario.unavailable = True
        await watchdog.check_once()
        assert not runtime.ready
        scenario.unavailable = False
        await watchdog.check_once()
        assert runtime.ready

        # 配置 Revision 变化后重新校验。
        scenario.revision = "revision-b"
        await watchdog.check_once()
        assert runtime.ready
        assert watchdog.point_mappings_ok
    finally:
        await commander.close()
        await source.close()


async def test_watchdog_metadata_mismatch_interlocks(orca_config, commander_server) -> None:
    config, device, source, commander, _, scenario, _ = \
        await assemble(orca_config, commander_server)
    # 配置声明 float32，Mock Commander 报告 int32：元数据不匹配必须闭锁。
    resource = config.resources[device]
    inputs = {
        name: variable.model_copy(update={"data_type": "float32"})
        for name, variable in resource.inputs.items()
    }
    config = config.model_copy(update={
        "resources": {device: resource.model_copy(update={"inputs": inputs})},
    })
    scenario.points[(device, "active_power")] = CommanderPoint(data_type="int32", writable=False)

    runtime = ControlRuntime(config, source)
    watchdog = WindHubWatchdog(config, runtime, source, commander)
    try:
        await source.connect()
        await commander.connect()
        await watchdog.check_once()
        assert not runtime.ready
        assert not watchdog.point_mappings_ok
        with pytest.raises(RuntimeError):
            await runtime.start_task(TASK)
    finally:
        await commander.close()
        await source.close()


async def test_runtime_task_lifecycle_and_cancel(orca_config, data_publisher, commander_server) -> None:
    config, device, source, commander, cycle, scenario, _ = \
        await assemble(orca_config, commander_server)
    runtime = ControlRuntime(config, source, cycle)
    try:
        await source.connect()
        await commander.connect()
        # 未就绪禁止启动。
        with pytest.raises(RuntimeError):
            await runtime.start_task(TASK)
        runtime.ready = True
        await data_publisher.publish(device, "active_power", 2.5)
        await runtime.start_task(TASK)
        assert runtime.states[TASK] == "RUNNING"
        await asyncio.sleep(1.5)
        # 输出权限未验证：运行中也不写设备，原因明确。
        assert not scenario.writes
        assert "output disabled" in runtime.reasons.get(TASK, "")
        # 取消任务并释放输出所有权。
        await runtime.stop_task(TASK)
        assert runtime.states[TASK] == "STOPPED"
        assert runtime.output_owner(device, "active_power_setpoint") is None
        await runtime.stop()
    finally:
        await commander.close()
        await source.close()
