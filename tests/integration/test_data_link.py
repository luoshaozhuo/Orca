"""数据链路：模拟发布器 → 真实 Redis → 生产 RedisSource。

Redis 服务由 scripts/start_redis.sh 独立管理；不可用时明确失败，不回退 Fake。
"""
from __future__ import annotations

import asyncio
import json

import pytest
import redis.asyncio as redis

from core.domain.config import RedisConfig
from infrastructure.redis.source import RedisSource, resolve_password
from tests.integration.conftest import TEST_DEVICE
from tests.support.redis_data import test_prefix as make_test_prefix

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


async def test_malformed_record_rejected_then_recovers(
    data_publisher, redis_client, redis_source,
) -> None:
    # 非法 JSON 与缺字段记录都是明确错误；修复后读取恢复，无残留状态。
    key = RedisSource.key(data_publisher.prefix, *PAIR)
    await redis_client.set(key, "{not-json", ex=60)
    with pytest.raises(ValueError):
        await redis_source.latest([PAIR])
    await redis_client.set(key, json.dumps({"value": 1.0}), ex=60)
    with pytest.raises(ValueError, match="malformed"):
        await redis_source.latest([PAIR])
    await data_publisher.publish(*PAIR, 3.0)
    assert (await redis_source.latest([PAIR]))[PAIR]["value"] == 3.0


async def test_concurrent_batch_reads_are_consistent(data_publisher, redis_source) -> None:
    pairs = [(TEST_DEVICE, f"concurrent_power_{index}") for index in range(16)]
    for index, pair in enumerate(pairs):
        await data_publisher.publish(*pair, float(index))
    expected = {pair: float(index) for index, pair in enumerate(pairs)}
    results = await asyncio.gather(*(redis_source.latest(pairs) for _ in range(16)))
    for records in results:
        assert {pair: record["value"] for pair, record in records.items()} == expected


async def test_client_close_and_reconnect_has_no_residual_state(
    data_publisher, redis_config: RedisConfig,
) -> None:
    config = redis_config.model_copy(
        update={"key_prefix": make_test_prefix(redis_config.key_prefix)}
    )
    source = RedisSource(config, timeout=2.0)
    try:
        with pytest.raises(ConnectionError):
            await source.latest([PAIR])
        await source.connect()
        await data_publisher.publish(*PAIR, 4.0)
        assert (await source.latest([PAIR]))[PAIR]["value"] == 4.0
        await source.close()
        with pytest.raises(ConnectionError):
            await source.latest([PAIR])
        # 重连后恢复读取，数据来自服务端而非客户端缓存。
        await source.connect()
        assert (await source.latest([PAIR]))[PAIR]["value"] == 4.0
    finally:
        await source.close()
