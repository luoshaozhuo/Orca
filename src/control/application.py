"""控制应用装配及进程内管理。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.redis.source import RedisSource

from core.domain.config import OrcaConfig
from core.application.reference_manager import ReferenceManager
from .runtime import ControlRuntime
from .cycle import ControlCycle
from algorithms.noop import NoOpStrategy
from infrastructure.wind_hub.watchdog import WindHubWatchdog


class ControlApplication:
    """负责 Runtime/Watchdog/Unix socket 的生命周期。"""

    def __init__(self, config: OrcaConfig) -> None:
        self.config = config
        self.references = ReferenceManager.from_controllers(config.controllers)
        timeout = config.wind_hub.watchdog.timeout
        self.redis = RedisSource(config.wind_hub.redis, timeout)
        self.commander = CommanderClient(config.wind_hub.commander.target, timeout)
        if any(controller.enabled and controller.strategy != "noop" for controller in config.controllers.values()):
            raise ValueError("unsupported strategy: only noop is available before field commissioning")
        self.cycle = ControlCycle(config, self.redis, self.commander, NoOpStrategy())
        self.runtime = ControlRuntime(config, self.redis, self.cycle)
        self.watchdog = WindHubWatchdog(config, self.runtime, self.redis, self.commander)
        self._server: asyncio.AbstractServer | None = None
        self._socket = Path(config.runtime.socket_path)

    async def start(self) -> None:
        if self._socket.exists():
            raise RuntimeError(f"IPC socket already exists: {self._socket}")
        self._server = await asyncio.start_unix_server(self._handle, path=str(self._socket))
        try:
            await self.watchdog.start()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        await self.watchdog.stop()
        await self.runtime.stop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._socket.unlink(missing_ok=True)
        await self.redis.close()
        await self.commander.close()

    def status(self) -> dict:
        return {
            "site_id": self.config.site.site_id,
            "ready": self.runtime.ready,
            "redis_ok": self.watchdog.redis_ok,
            "commander_ok": self.watchdog.commander_ok,
            "point_mappings_ok": self.watchdog.point_mappings_ok,
            "output_access_verified": self.watchdog.output_access_verified,
            "last_error": self.watchdog.last_error,
            "tasks": self.runtime.states.copy(),
            "output_owners": {f"{device}/{point}": owner for (device, point), owner in self.runtime._authority.items()},
            "task_reasons": self.runtime.reasons.copy(),
        }

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            payload = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request = json.loads(payload)
            action = request.get("action")
            if action == "status":
                result = self.status()
            elif action == "start":
                await self.runtime.start_task(str(request["task"]))
                result = self.status()
            elif action == "stop":
                if request.get("all"):
                    await self.runtime.stop()
                else:
                    await self.runtime.stop_task(str(request["task"]))
                result = self.status()
            else:
                raise ValueError(f"unknown operation: {action}")
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        finally:
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            try:
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
