"""使用真实 Redis 客户端发布 wind-hub 格式的模拟测点数据。"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from infrastructure.redis.source import RedisSource


async def publish_measurement(
    client: redis.Redis,
    device_id: str,
    point_id: str,
    value: float,
    *,
    prefix: str,
    quality: str = "good",
    age_seconds: float = 0.0,
) -> None:
    record = {
        "value": value,
        "quality": quality,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(),
        "source": "test-simulator",
    }
    await client.set(
        RedisSource.key(prefix, device_id, point_id),
        json.dumps(record),
    )
