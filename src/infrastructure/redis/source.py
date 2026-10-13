"""读取 wind-hub Redis Sink 最新值；该 Sink 使用 SET，不发布 Pub/Sub。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis

from core.domain.config import RedisConfig


def resolve_password(config: RedisConfig) -> str | None:
    """密码优先级：password_env 环境变量（非空） > password_file 文件 > None。

    启动脚本生成的密码文件对 Orca 进程不可见为环境变量，因此允许显式配置文件路径。
    """
    if config.password_env:
        password = os.environ.get(config.password_env)
        if password:
            return password
    if config.password_file:
        path = Path(config.password_file).expanduser()
        if path.is_file():
            password = path.read_text(encoding="utf-8").strip("\r\n")
            if password:
                return password
    return None


class RedisSource:
    """使用长度前缀键名，兼容 wind-hub redis_sink_record.py。"""

    def __init__(self, config: RedisConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        await self.close()
        password = resolve_password(self._config)
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
            datetime.fromisoformat(str(payload["timestamp"]))
            result[pair] = payload
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
