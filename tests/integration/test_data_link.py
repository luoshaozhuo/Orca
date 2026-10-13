"""数据链路：模拟发布器 → 真实 Redis → 生产 RedisSource。

Redis 服务由 scripts/start_redis.sh 独立管理；不可用时明确失败，不回退 Fake。
"""
from __future__ import annotations

import asyncio

import pytest
import redis.asyncio as redis

from core.domain.config import RedisConfig
from infrastructure.redis.source import RedisSource, resolve_password
from tests.integration.conftest import TEST_DEVICE

# 专用测点：与控制链测试的键完全隔离（共享 Redis，键不重叠）。
PAIR = (TEST_DEVICE, "link_check_power")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.real_service,
]


async def test_publish_and_read_latest(data_publisher, redis_source) -> None:
    await data_publisher.publish(*PAIR, 1.75)
    records = await redis_source.latest([PAIR])
    assert records[PAIR]["value"] == 1.75
    assert records[PAIR]["quality"] == "good"
    assert records[PAIR]["source"] == "test-simulator"


async def test_missing_measurement_returns_empty(data_publisher, redis_source) -> None:
    records = await redis_source.latest([(TEST_DEVICE, "never_published_point")])
    assert records == {}


async def test_bad_quality_and_stale_are_visible(data_publisher, redis_source) -> None:
    await data_publisher.publish(*PAIR, 2.0, quality="bad", age_seconds=60)
    records = await redis_source.latest([PAIR])
    assert records[PAIR]["quality"] == "bad"


async def test_illegal_timestamp_rejected(data_publisher, redis_source) -> None:
    await data_publisher.publish(*PAIR, 1.0, timestamp="not-a-timestamp")
    with pytest.raises(ValueError):
        await redis_source.latest([PAIR])


async def test_wrong_password_fails_clearly(redis_config: RedisConfig) -> None:
    client = redis.Redis(
        host=redis_config.host, port=redis_config.port, db=redis_config.database,
        username=redis_config.username, password="0" * 32, decode_responses=True,
        socket_connect_timeout=2.0, socket_timeout=2.0,
    )
    try:
        with pytest.raises(redis.RedisError):
            await client.ping()
    finally:
        await client.aclose()


async def test_wrong_username_fails_clearly(redis_config: RedisConfig) -> None:
    client = redis.Redis(
        host=redis_config.host, port=redis_config.port, db=redis_config.database,
        username="nobody", password=resolve_password(redis_config),
        decode_responses=True, socket_connect_timeout=2.0, socket_timeout=2.0,
    )
    try:
        with pytest.raises(redis.RedisError):
            await client.ping()
    finally:
        await client.aclose()


async def test_redis_unreachable_fails_clearly(redis_config: RedisConfig) -> None:
    # 不可路由地址 + 有界超时：验证连接失败是明确错误而非挂起。
    unreachable = redis_config.model_copy(update={"host": "10.255.255.1"})
    source = RedisSource(unreachable, timeout=0.5)
    with pytest.raises((ConnectionError, TimeoutError, redis.RedisError)):
        await source.connect()


async def test_acl_denies_dangerous_commands(redis_client) -> None:
    # 最小权限账户不得执行管理命令或删除数据。
    with pytest.raises(redis.RedisError):
        await redis_client.config_get("dir")
    with pytest.raises(redis.RedisError):
        await redis_client.delete("windhub-test:should:not:be:deletable")


async def test_published_keys_expire_automatically(data_publisher, redis_source) -> None:
    # 最小 ACL 不含 TTL 命令，通过短 TTL + GET 验证自动过期。
    await data_publisher.publish(*PAIR, 9.9, ttl_seconds=1)
    await asyncio.sleep(1.5)
    assert await redis_source.latest([PAIR]) == {}
