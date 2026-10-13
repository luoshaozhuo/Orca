"""控制应用装配及进程内管理。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from infrastructure.wind_hub.commander import CommanderClient
from infrastructure.redis.source import RedisSource

from core.application.control_coordinator import ControlCoordinator
from core.application.active_power_controller import ActivePowerController
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.application.port.planning_snapshot import PlanningSnapshotPort
from core.application.control_pipeline_factory import build_control_pipeline
from core.application.internal_reference import InternalReferenceService
from core.application.reference_manager import ReferenceManager
from core.domain.config import OrcaConfig
from core.domain.control_authority import ControlFunction
from core.domain.control_value import ControlQuantity
from infrastructure.redis.planning_snapshot import (
    ActivePlanningBinding,
    RedisPlanningSnapshotSource,
)
from .runtime import ControlRuntime
from .cycle import ControlCycle
from algorithms.noop import NoOpStrategy
from infrastructure.wind_hub.watchdog import WindHubWatchdog


class ControlApplication:
    """负责 Runtime/Watchdog/Unix socket 的生命周期。"""

    def __init__(
        self,
        config: OrcaConfig,
        *,
        planning_snapshots: PlanningSnapshotPort | None = None,
        planning_tasks: frozenset[str] = frozenset(),
        planning_bindings: dict[str, ActivePlanningBinding] | None = None,
    ) -> None:
        self.config = config
        self.references = ReferenceManager.from_controllers(config.controllers)
        self.active_power_coordinator = ControlCoordinator(
            config.coordination.active_power
        )
        # 算法状态按任务隔离；共享 Controller 配置不意味着共享积分状态。
        self.control_pipelines = {
            task_id: build_control_pipeline(
                config.controllers[task.controller_id].pipeline
            )
            for task_id, task in config.tasks.items()
            if task.enabled
        }
        timeout = config.wind_hub.watchdog.timeout
        self.redis = RedisSource(config.wind_hub.redis, timeout)
        self.commander = CommanderClient(config.wind_hub.commander.target, timeout)
        if any(controller.enabled and controller.strategy != "noop" for controller in config.controllers.values()):
            raise ValueError("unsupported strategy: only noop is available before field commissioning")
        self.cycle = ControlCycle(config, self.redis, self.commander, NoOpStrategy())
        if planning_bindings is not None and planning_snapshots is not None:
            raise ValueError("select a single planning snapshot source")
        if planning_bindings is not None:
            if set(planning_bindings) != planning_tasks:
                raise ValueError("planning bindings must match planning tasks")
            planning_snapshots = RedisPlanningSnapshotSource(
                self.redis,
                self.references,
                planning_bindings,
                generation=0,
                maximum_age_ns=int(config.wind_hub.watchdog.interval * 1e9),
                can_plan=self._can_plan_device,
            )
        if planning_tasks and planning_snapshots is None:
            raise ValueError("planning tasks require an authorized snapshot source")
        if planning_tasks - set(self.control_pipelines):
            raise ValueError("planning tasks require enabled configured controllers")
        planning_cycle = None
        if planning_snapshots is not None and planning_tasks:
            planning_cycle = ReadOnlyPlanningCycle(
                planning_snapshots,
                {
                    task_id: ActivePowerController(
                        pipeline=self.control_pipelines[task_id],
                        coordinator=self.active_power_coordinator,
                    )
                    for task_id in planning_tasks
                },
                JointPowerAllocator(),
                maximum_age_ns=int(config.wind_hub.watchdog.interval * 1e9),
            )
        self.runtime = ControlRuntime(
            config, self.redis, self.cycle,
            reset_control_state=self._reset_control_state,
            planning_cycle=planning_cycle,
            planning_tasks=planning_tasks,
        )
        self.internal_references = InternalReferenceService(
            self.references, self.runtime.control_authority,
        )
        self.watchdog = WindHubWatchdog(config, self.runtime, self.redis, self.commander)
        self._server: asyncio.AbstractServer | None = None
        self._socket = Path(config.runtime.socket_path)

    def _can_plan_device(
        self,
        task_id: str,
        device_id: str,
        quantity: ControlQuantity,
    ) -> bool:
        if task_id not in self.config.tasks:
            return False
        task = self.config.tasks[task_id]
        if device_id not in task.resource_ids:
            return False
        if not self.runtime.ready or self.runtime.states[task_id] != "RUNNING":
            return False
        function = (
            ControlFunction.AGC
            if quantity == ControlQuantity.ACTIVE_POWER
            else ControlFunction.AVC
        )
        return self.runtime.control_authority.can_output(function)

    def _reset_control_state(self, task_id: str) -> None:
        pipeline = self.control_pipelines.get(task_id)
        if pipeline is not None:
            pipeline.reset()

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
            "planning_tasks": sorted(self.runtime._planning_tasks),
            "planning_results": {
                task_id: {
                    "generation": result.allocation.generation,
                    "feasible": result.allocation.feasible,
                    "active_setpoints": len(result.allocation.active),
                    "reactive_setpoints": len(result.allocation.reactive),
                }
                for task_id, result in self.runtime.planning_results.items()
                if self.runtime.ready and self.runtime.states[task_id] == "RUNNING"
                and self.runtime.control_authority.can_output(ControlFunction.AGC)
            },
            "authority_mode": self.runtime.control_authority.mode.value,
            "control_functions": {
                function.value: {
                    "service": self.runtime.control_authority.functions[function].value,
                    "blocked": sorted(self.runtime.control_authority.blocks[function]),
                    "can_output": self.runtime.control_authority.can_output(function),
                }
                for function in self.runtime.control_authority.functions
            },
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
