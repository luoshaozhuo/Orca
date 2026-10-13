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
from core.domain.config import ControlPipelineConfig, OrcaConfig
from core.domain.control_authority import ControlFunction
from core.domain.control_value import ControlQuantity
from core.domain.coordination import CoordinationFunction
from core.domain.device_capability import DeviceTechnology
from infrastructure.redis.planning_snapshot import (
    ActivePlanningBinding,
    DevicePlanningBinding,
    PlanningReferenceBinding,
    RedisPlanningSnapshotSource,
    StoragePlanningBinding,
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
        if planning_bindings is None and planning_snapshots is None and not planning_tasks:
            configured = {
                task_id: (task, task.planning)
                for task_id, task in config.tasks.items()
                if task.enabled and task.planning is not None
            }
            planning_tasks = frozenset(configured)
            planning_bindings = {
                task_id: ActivePlanningBinding(
                    target=plan.target,
                    poi_device_id=plan.poi_device_id,
                    poi_active_point=plan.poi_active_point,
                    minimum=plan.minimum,
                    maximum=plan.maximum,
                    dt_s=task.interval,
                    authorized_sources=frozenset(plan.authorized_sources),
                    active_functions=frozenset(plan.active_functions),
                    reference_inputs=tuple(
                        PlanningReferenceBinding(
                            purpose=reference.purpose,
                            source=reference.source,
                            resource_id=reference.resource_id,
                            point_id=reference.point_id,
                            valid_for_ns=int(reference.valid_for_s * 1e9),
                        )
                        for reference in plan.reference_inputs
                    ),
                    maximum_skew_ns=plan.maximum_skew_ns,
                    devices=tuple(
                        DevicePlanningBinding(
                            device_id=device.device_id,
                            p_point=device.p_point,
                            q_point=device.q_point,
                            technology=DeviceTechnology(device.technology),
                            p_min=device.p_min, p_max=device.p_max,
                            q_min=device.q_min, q_max=device.q_max,
                            s_max=device.s_max,
                            p_auto=device.p_auto, q_auto=device.q_auto,
                            p_weight=device.p_weight, q_weight=device.q_weight,
                            p_ramp_up=device.p_ramp_up,
                            p_ramp_down=device.p_ramp_down,
                            q_ramp_up=device.q_ramp_up,
                            q_ramp_down=device.q_ramp_down,
                            storage=(
                                StoragePlanningBinding(
                                    soc_point=device.storage.soc_point,
                                    soc_min=device.storage.soc_min,
                                    soc_max=device.storage.soc_max,
                                    energy_mwh=device.storage.energy_mwh,
                                    charge_efficiency=device.storage.charge_efficiency,
                                    discharge_efficiency=device.storage.discharge_efficiency,
                                )
                                if device.storage is not None else None
                            ),
                        )
                        for device in plan.devices
                    ),
                )
                for task_id, (task, plan) in configured.items()
            }

        if planning_bindings is not None and planning_snapshots is not None:
            raise ValueError("select a single planning snapshot source")
        if planning_bindings is not None:
            if set(planning_bindings) != planning_tasks:
                raise ValueError("planning bindings must match planning tasks")
            planning_snapshots = RedisPlanningSnapshotSource(
                self.redis,
                self.references,
                planning_bindings,
                generation=config.runtime.planning_generation,
                maximum_age_ns=int(config.wind_hub.watchdog.interval * 1e9),
                can_plan=self._can_plan_device,
                can_contribute=self._can_contribute,
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
                permitted=self._can_publish_plan,
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

    def _can_publish_plan(self, task_id: str) -> bool:
        return (
            task_id in self.config.tasks
            and self.runtime.ready
            and self.runtime.states[task_id] == "RUNNING"
            and self.runtime.control_authority.can_output(ControlFunction.AGC)
        )

    def _can_contribute(
        self,
        task_id: str,
        function: CoordinationFunction,
    ) -> bool:
        if task_id not in self.config.tasks:
            return False
        if not self.runtime.ready or self.runtime.states[task_id] != "RUNNING":
            return False
        if function == CoordinationFunction.PFR:
            control_function = ControlFunction.PFR
        elif function == CoordinationFunction.IR:
            control_function = ControlFunction.IR
        else:
            return False
        return self.runtime.control_authority.can_output(control_function)

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
        if quantity == ControlQuantity.ACTIVE_POWER:
            function = ControlFunction.AGC
        elif quantity == ControlQuantity.REACTIVE_POWER:
            function = ControlFunction.AVC
        else:
            return False
        return self.runtime.control_authority.can_output(function)

    def _reset_control_state(self, task_id: str) -> None:
        pipeline = self.control_pipelines.get(task_id)
        if pipeline is not None:
            pipeline.reset()

    def replace_task_pipeline(
        self,
        task_id: str,
        config: ControlPipelineConfig,
        *,
        authorized: bool,
    ) -> None:
        """停止状态下替换算法；旧状态不得迁移或隐式恢复自动任务。"""
        if not authorized:
            raise PermissionError("pipeline change requires authorization")
        if task_id not in self.control_pipelines:
            raise ValueError(f"unknown or disabled pipeline task: {task_id}")
        if self.runtime.states[task_id] != "STOPPED":
            raise RuntimeError("pipeline switch requires a stopped task")
        updated = build_control_pipeline(config)
        old = self.control_pipelines[task_id]
        old.reset()
        self.control_pipelines[task_id] = updated
        self.runtime.planning_results.pop(task_id, None)
        planning = self.runtime._planning_cycle
        if isinstance(planning, ReadOnlyPlanningCycle) and task_id in self.runtime._planning_tasks:
            planning.replace_controller(
                task_id,
                ActivePowerController(updated, self.active_power_coordinator),
            )

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
