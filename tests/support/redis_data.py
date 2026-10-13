"""模拟数据发布器：以 wind-hub Redis Sink 相同的 Key 与结构写入真实 Redis。

隔离约定（测试不管理 Redis 服务生命周期，不 FLUSHDB、不 DEL）：
- 测试 Key 命名空间为 ``<key_prefix>-test``，测试设备 ID 以 ``test-`` 前缀区分；
- 所有写入带 TTL，到期自动过期，不清理不属于当前测试的 Key。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from core.domain.config import RedisConfig
from infrastructure.redis.source import RedisSource, resolve_password

TEST_DEVICE_PREFIX = "test-"
DEFAULT_TTL_SECONDS = 300


def test_prefix(key_prefix: str) -> str:
    return f"{key_prefix}-test"


def make_client(config: RedisConfig, timeout: float = 2.0) -> redis.Redis:
    """连接参数与密码全部来自正式配置与生产 resolve_password()。"""
    return redis.Redis(
        host=config.host,
        port=config.port,
        db=config.database,
        username=config.username,
        password=resolve_password(config),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        decode_responses=True,
    )


class DataPublisher:
    """由 pytest fixture 管理生命周期，供多个集成测试复用。"""

    def __init__(self, client: redis.Redis, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    @property
    def prefix(self) -> str:
        return self._prefix

    async def publish(
        self,
        device_id: str,
        point_id: str,
        value: object,
        *,
        quality: str = "good",
        age_seconds: float = 0.0,
        source: str = "test-simulator",
        timestamp: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        """发布任意设备/测点；支持 quality、timestamp、source 及过期时间。"""
        record = {
            "value": value,
            "quality": quality,
            "timestamp": timestamp
            or (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(),
            "source": source,
        }
        await self._client.set(
            RedisSource.key(self._prefix, device_id, point_id),
            json.dumps(record),
            ex=ttl_seconds,
        )
