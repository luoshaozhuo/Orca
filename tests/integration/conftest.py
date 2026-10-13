"""集成测试 Fixture：真实 Redis + Mock gRPC Server + 真实 Orca 客户端。

Redis 连接配置唯一来源为 configs/system.yaml 的 wind_hub.redis，
密码复用生产 resolve_password()；Redis 服务由 scripts/start_redis.sh
独立管理，测试只管理客户端连接生命周期，不可用时明确失败。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import redis.asyncio as redis

from core.domain.config import OrcaConfig, RedisConfig
from infrastructure.config.yaml import load_config
from infrastructure.redis.source import RedisSource
from tests.support.collector import CollectorScenario, start_collector
from tests.support.commander import CommanderScenario, start_commander
from tests.support.redis_data import DataPublisher, make_client, test_prefix

CONFIG = Path(__file__).resolve().parents[2] / "configs"
TEST_DEVICE = "test-wtg001"


def derive_test_config(config: OrcaConfig, device_id: str = TEST_DEVICE) -> OrcaConfig:
    """从正式配置派生控制链测试配置：测试 Key 命名空间 + 专用测试设备 ID。

    连接参数不变；resources/tasks 中的设备替换为测试设备，避免污染生产数据。
    """
    redis_cfg = config.wind_hub.redis.model_copy(
        update={"key_prefix": test_prefix(config.wind_hub.redis.key_prefix)}
    )
    wind_hub = config.wind_hub.model_copy(update={"redis": redis_cfg})
    resources = {device_id: config.resources["wtg001"]}
    tasks = {
        name: task.model_copy(update={"resource_ids": [device_id]})
        for name, task in config.tasks.items()
    }
    return config.model_copy(
        update={"wind_hub": wind_hub, "resources": resources, "tasks": tasks}
    )


@pytest.fixture
def orca_config() -> OrcaConfig:
    return load_config(CONFIG)


@pytest.fixture
def redis_config(orca_config: OrcaConfig) -> RedisConfig:
    return orca_config.wind_hub.redis


@pytest_asyncio.fixture
async def redis_client(redis_config: RedisConfig):
    client = make_client(redis_config)
    try:
        await client.ping()
    except redis.RedisError as exc:
        await client.aclose()
        pytest.fail(
            f"integration environment dependency failed: cannot authenticate to the "
            f"managed Redis at {redis_config.host}:{redis_config.port} "
            f"(deploy it with scripts/start_redis.sh): {exc}"
        )
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def data_publisher(redis_client, redis_config: RedisConfig) -> DataPublisher:
    return DataPublisher(redis_client, test_prefix(redis_config.key_prefix))


@pytest.fixture
def test_config(orca_config: OrcaConfig) -> OrcaConfig:
    return derive_test_config(orca_config)


@pytest_asyncio.fixture
async def redis_source(test_config: OrcaConfig) -> RedisSource:
    """使用测试命名空间的生产 RedisSource，避免污染采集数据。"""
    source = RedisSource(test_config.wind_hub.redis, timeout=2.0)
    await source.connect()
    try:
        yield source
    finally:
        await source.close()


@pytest_asyncio.fixture
async def collector_server():
    """工厂：每个测试用独立场景启动 Mock Collector，互不共享状态。"""
    servers = []

    async def start(scenario: CollectorScenario | None = None):
        scenario = scenario or CollectorScenario()
        server, target = await start_collector(scenario)
        servers.append(server)
        return target, scenario

    yield start
    for server in servers:
        await server.stop(grace=0)


@pytest_asyncio.fixture
async def commander_server():
    """工厂：每个测试用独立场景启动 Mock Commander，互不共享状态。"""
    servers = []

    async def start(scenario: CommanderScenario | None = None):
        scenario = scenario or CommanderScenario()
        server, target = await start_commander(scenario)
        servers.append(server)
        return target, scenario

    yield start
    for server in servers:
        await server.stop(grace=0)
