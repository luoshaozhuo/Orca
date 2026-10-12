"""外部依赖看门狗：连接检测、重连、配置变化后重新验证。"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from core.domain.config import OrcaConfig
from control.runtime import ControlRuntime

logger = logging.getLogger(__name__)


class RedisPort(Protocol):
    async def ping(self) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class CommanderPort(Protocol):
    async def status(self) -> tuple[bool, str]: ...
    async def devices(self) -> set[str]: ...
    async def resolve_point(self, device_id: str, point_id: str) -> str: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class WindHubWatchdog:
    """周期巡检外部依赖；任务预检查由运行时自行完成。"""

    def __init__(
        self, config: OrcaConfig, runtime: ControlRuntime, redis: RedisPort, commander: CommanderPort
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.redis = redis
        self.commander = commander
        self.redis_ok = False
        self.commander_ok = False
        self.point_mappings_ok = False
        self.output_access_verified = False  # Commander ResolvePoint 不暴露可写权限
        self.last_error: str | None = None
        self._revision: str | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            await self.check_once()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.config.wind_hub.watchdog.interval)
            await self.check_once()

    async def check_once(self) -> None:
        # 健康巡检期间保守禁止写入，防止连接变化时继续使用旧映射。
        self.runtime.ready = False
        self.last_error = None
        try:
            await self.redis.ping()
            self.redis_ok = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.redis_ok = False
            try:
                await self.redis.connect()
                self.redis_ok = True
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                self.last_error = f"Redis: {retry_exc}"
                logger.warning("Redis unavailable: %s", retry_exc)

        try:
            running, revision = await self.commander.status()
            if not running:
                raise ConnectionError("Commander is not running")
            self.commander_ok = True
            if revision != self._revision or not self.point_mappings_ok:
                self.runtime.ready = False
                self.point_mappings_ok = False
                await self._verify_mappings()
                self._revision = revision
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.commander_ok = False
            self.point_mappings_ok = False
            self.runtime.ready = False
            try:
                await self.commander.connect()
                running, revision = await self.commander.status()
                if not running:
                    raise ConnectionError("Commander is not running")
                self.commander_ok = True
                await self._verify_mappings()
                self._revision = revision
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                self.last_error = f"Commander/mappings: {retry_exc}"
                logger.warning("Commander unavailable or mappings invalid: %s", retry_exc)

        self.runtime.ready = self.redis_ok and self.commander_ok and self.point_mappings_ok

    async def _verify_mappings(self) -> None:
        devices = await self.commander.devices()
        for device_id, resource in self.config.resources.items():
            if not resource.enabled:
                continue
            if device_id not in devices:
                raise ValueError(f"Commander missing enabled device {device_id}")
            for variable in (*resource.inputs.values(), *resource.outputs.values()):
                actual_type = await self.commander.resolve_point(device_id, variable.point_id)
                if variable.data_type is not None and actual_type != variable.data_type:
                    raise ValueError(
                        f"{device_id}/{variable.point_id}: type {actual_type}, "
                        f"expected {variable.data_type}"
                    )
        self.point_mappings_ok = True
