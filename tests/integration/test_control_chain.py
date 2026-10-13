"""Runtime 与看门狗集成测试：真实 Redis + 生产 RedisSource/CommanderClient + Mock Commander。

配置由 configs/system.yaml 派生（测试命名空间 + 每测试唯一设备 ID）。
非规划任务只读预检查：任何情况下不产生设备写入。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from control.runtime import ControlRuntime
from infrastructure.redis.source import RedisSource
from infrastructure.wind_hub.commander import CommanderClient
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
    return config, device, source, commander, scenario


async def test_read_only_task_never_writes(orca_config, data_publisher, commander_server) -> None:
    config, device, source, commander, scenario = await assemble(orca_config, commander_server)
    runtime = ControlRuntime(config, source)
    pair = (device, "active_power")
    try:
        await source.connect()
        await commander.connect()
        runtime.ready = True
        await runtime.start_task(TASK)

        # 缺测、坏质量、过期均记录原因；恢复后原因清除。
        await asyncio.sleep(0.2)
        assert "missing" in runtime.reasons.get(TASK, "")
        await data_publisher.publish(*pair, 1.5, quality="bad")
        await asyncio.sleep(1.2)
        assert "bad quality" in runtime.reasons.get(TASK, "")
        await data_publisher.publish(*pair, 1.5, age_seconds=60)
        await asyncio.sleep(1.2)
        assert "stale" in runtime.reasons.get(TASK, "")
        await data_publisher.publish(*pair, 1.75)
        await asyncio.sleep(1.2)
        assert TASK not in runtime.reasons
        assert scenario.writes == []
    finally:
        await runtime.stop()
        await commander.close()
        await source.close()


async def test_watchdog_health_and_revision_revalidation(orca_config, commander_server) -> None:
    config, _, source, commander, scenario = await assemble(orca_config, commander_server)
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
    config, device, source, commander, scenario = await assemble(orca_config, commander_server)
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
    config, device, source, commander, scenario = await assemble(orca_config, commander_server)
    runtime = ControlRuntime(config, source)
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
        # 只读任务：运行中也不写设备。
        assert not scenario.writes
        # 取消任务并释放输出所有权。
        await runtime.stop_task(TASK)
        assert runtime.states[TASK] == "STOPPED"
        assert runtime.output_owner(device, "active_power_setpoint") is None
        await runtime.stop()
    finally:
        await commander.close()
        await source.close()
