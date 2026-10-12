"""读取 wind-hub Redis Sink 最新值；该 Sink 使用 SET，不发布 Pub/Sub。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from core.domain.config import RedisConfig


class RedisSource:
    """使用长度前缀键名，兼容 wind-hub redis_sink_record.py。"""

    def __init__(self, config: RedisConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        await self.close()
        password = os.environ.get(self._config.password_env) if self._config.password_env else None
        self._client = redis.Redis(
            host=self._config.host,
            port=self._config.port,
            db=self._config.database,
            username=self._config.username,
            password=password,
            socket_connect_timeout=self._timeout,
            socket_timeout=self._timeout,
            decode_responses=True,
        )
        await self.ping()

    async def ping(self) -> None:
        if self._client is None:
            raise ConnectionError("Redis is not connected")
        if not await self._client.ping():
            raise ConnectionError("Redis PING failed")

    @staticmethod
    def key(prefix: str, device_id: str, point_id: str) -> str:
        return f"{prefix}:{len(device_id)}:{device_id}:{len(point_id)}:{point_id}"

    async def latest(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        if self._client is None:
            raise ConnectionError("Redis is not connected")
        if not pairs:
            return {}
        keys = [self.key(self._config.key_prefix, d, p) for d, p in pairs]
        values = await self._client.mget(keys)
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for pair, raw in zip(pairs, values, strict=True):
            if raw is None:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not {"value", "quality", "timestamp"} <= payload.keys():
                raise ValueError(f"malformed Redis record for {pair}")
            datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00"))
            result[pair] = payload
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
