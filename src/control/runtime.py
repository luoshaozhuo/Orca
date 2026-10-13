"""Orca 生命周期：服务可运行，任务默认停止；首阶段不下发控制指令。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

from core.application.validation import validate_inputs
from core.domain.config import OrcaConfig
from core.domain.control_authority import ControlAuthority, ControlFunction

from .cycle import ControlCycle

if TYPE_CHECKING:
    from core.application.read_only_planning_cycle import (
        PlanningCyclePort,
        PlanningCycleResult,
    )
    from core.application.safe_plan_dispatcher import SafePlanDispatcher

class LatestSource(Protocol):
    async def latest(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        ...


logger = logging.getLogger(__name__)


class TaskState(StrEnum):
    """任务生命周期状态；字符串值保持对外状态接口兼容。"""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"



class ControlRuntime:
    """独立任务顺序执行；首阶段仅实现预检查和空控制循环。"""

    def __init__(
        self,
        config: OrcaConfig,
        source: object,
        cycle: ControlCycle | None = None,
        *,
        reset_control_state: Callable[[str], None] | None = None,
        planning_cycle: PlanningCyclePort | None = None,
        planning_tasks: frozenset[str] = frozenset(),
        dispatcher: SafePlanDispatcher | None = None,
    ) -> None:
        self.config = config
        self.control_authority = ControlAuthority()
        self.source = source
        self.cycle = cycle
        self._reset_control_state = reset_control_state
        if planning_tasks and planning_cycle is None:
            raise ValueError("planning tasks require a planning cycle")
        if planning_tasks - set(config.tasks):
            raise ValueError("unknown planning task")
        self._planning_cycle = planning_cycle
        self._planning_tasks = planning_tasks
        self._dispatcher = dispatcher
        self.dispatch_blocked: dict[str, str] = {}
        self._inflight_dispatch: set[str] = set()
        self.planning_results: dict[str, PlanningCycleResult] = {}
        self.write_enabled = False  # 现场写权限未验证；生产装配不得自动放开
        self.states = {name: TaskState.STOPPED for name in config.tasks}
        self.reasons: dict[str, str] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.ready = False
        self._authority: dict[tuple[str, str], str] = {}

    def task_functions(self, task_id: str) -> frozenset[ControlFunction]:
        """任务投运所需全部控制功能；规划任务由 P/Q 配置决定。"""
        task = self.config.tasks[task_id]
        functions: set[ControlFunction] = set()
        if task.planning is not None:
            functions.add(ControlFunction.AGC)
        if task.reactive_planning is not None:
            functions.add(ControlFunction.AVC)
        if not functions:
            controller = self.config.controllers[task.controller_id]
            try:
                functions.add(ControlFunction(controller.type.upper()))
            except ValueError:
                pass
        return frozenset(functions)

    def _can_write_task(self, task_id: str) -> bool:
        functions = self.task_functions(task_id)
        return (
            self.write_enabled
            and self.ready
            and bool(functions)
            and all(self.control_authority.can_output(f) for f in functions)
            and self.states[task_id] == TaskState.RUNNING
            and task_id not in self.dispatch_blocked
        )

    async def enter_manual(self, *, authorized: bool) -> None:
        """停止所有自动任务、释放资源后才可移交人工控制权。"""
        if not authorized:
            raise PermissionError("manual handover requires authorization")
        if any(self.control_authority.in_service(f) for f in ControlFunction):
            raise RuntimeError("all automatic functions must be out of service")
        await self.stop()
        self.control_authority.enter_manual(
            authorized=True, outputs_released=not self._authority,
        )

    def output_owner(self, device_id: str, point_id: str) -> str | None:
        """按物理输出测点查询当前控制任务。"""
        return self._authority.get((device_id, point_id))

    def output_owners(self) -> dict[tuple[str, str], str]:
        """全部输出测点归属的只读快照。"""
        return dict(self._authority)

    @property
    def planning_cycle_port(self) -> PlanningCyclePort | None:
        """已装配的规划周期端口；仅用于受控装配操作。"""
        return self._planning_cycle

    @property
    def planning_task_ids(self) -> frozenset[str]:
        """声明为规划任务的任务集合。"""
        return self._planning_tasks

    async def start_task(self, task_id: str) -> None:
        if task_id not in self.states:
            raise ValueError(f"unknown task: {task_id}")
        if not self.config.tasks[task_id].enabled:
            raise ValueError(f"disabled task: {task_id}")
        if task_id in self.dispatch_blocked:
            raise RuntimeError("dispatch blocked: explicit clearance required")
        if self.states[task_id] != TaskState.STOPPED:
            return
        if not self.ready:
            raise RuntimeError("dependencies or point mappings are not verified")
        targets = {
            (device_id, output.point_id)
            for device_id in self.config.tasks[task_id].resource_ids
            for output in self.config.resources[device_id].outputs.values()
        }
        for target in targets:
            if owner := self._authority.get(target):
                raise RuntimeError(f"output {target} is already owned by task {owner}")
        self._reset_task_state(task_id)
        for target in targets:
            self._authority[target] = task_id
        self.states[task_id] = TaskState.RUNNING
        self._workers[task_id] = asyncio.create_task(self._run_task(task_id))

    def install_dispatcher(self, dispatcher: SafePlanDispatcher) -> None:
        """只能在全部任务停止且写入关闭时安装已校验执行器。"""
        if self.write_enabled or any(state != TaskState.STOPPED for state in self.states.values()):
            raise RuntimeError("dispatcher installation requires disabled writes and stopped tasks")
        self._dispatcher = dispatcher

    def clear_dispatch_block(self, task_id: str, *, authorized: bool) -> None:
        """仅任务停止后人工授权清除 UNKNOWN 闭锁，不自动补发。"""
        if not authorized:
            raise PermissionError("dispatch unblock requires authorization")
        if task_id not in self.states or self.states[task_id] != TaskState.STOPPED:
            raise RuntimeError("dispatch unblock requires stopped task")
        self.dispatch_blocked.pop(task_id, None)

    def _block_dispatch(self, task_id: str, reason: str) -> None:
        self.dispatch_blocked[task_id] = reason
        self.reasons[task_id] = reason
        self.write_enabled = False
        self.planning_results.pop(task_id, None)

    def _reset_task_state(self, task_id: str) -> None:
        self.planning_results.pop(task_id, None)
        if self._planning_cycle is not None and task_id in self._planning_tasks:
            self._planning_cycle.reset(task_id)
        if self._reset_control_state is not None:
            self._reset_control_state(task_id)

    async def stop_task(self, task_id: str) -> None:
        if task_id not in self.states:
            raise ValueError(f"unknown task: {task_id}")
        worker = self._workers.pop(task_id, None)
        if worker is not None:
            if task_id in self._inflight_dispatch:
                self._block_dispatch(task_id, "in-flight Commander write interrupted; outcome UNKNOWN")
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self.states[task_id] = TaskState.STOPPED
        self._release_authority(task_id)
        self._reset_task_state(task_id)

    def _release_authority(self, task_id: str) -> None:
        for target, owner in list(self._authority.items()):
            if owner == task_id:
                del self._authority[target]

    async def stop(self) -> None:
        for task_id in list(self.states):
            await self.stop_task(task_id)

    async def _run_task(self, task_id: str) -> None:
        """周期顺序执行，超期跳过触发点，不积压历史控制指令。"""
        interval = self.config.tasks[task_id].interval
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            while True:
                await asyncio.sleep(max(0.0, deadline - loop.time()))
                deadline = loop.time() + interval
                if task_id in self.dispatch_blocked:
                    self.reasons[task_id] = self.dispatch_blocked[task_id]
                    self._reset_task_state(task_id)
                    continue
                if not self.ready:
                    self.reasons[task_id] = "external dependencies unavailable"
                    self._reset_task_state(task_id)
                    continue
                try:
                    pairs = [
                        (resource_id, variable.point_id)
                        for resource_id in self.config.tasks[task_id].resource_ids
                        for variable in self.config.resources[resource_id].inputs.values()
                    ]
                    if task_id in self._planning_tasks:
                        assert self._planning_cycle is not None
                        result = await self._planning_cycle.execute(task_id)
                        self.planning_results[task_id] = result
                        if self._dispatcher is not None and self.write_enabled:
                            self._inflight_dispatch.add(task_id)
                            try:
                                receipt = await self._dispatcher.dispatch(task_id, result)
                            except asyncio.CancelledError:
                                self._block_dispatch(
                                    task_id,
                                    "in-flight Commander dispatch cancelled; outcome UNKNOWN",
                                )
                                raise
                            finally:
                                self._inflight_dispatch.discard(task_id)
                            if receipt.status != "CONFIRMED_SUCCESS":
                                reason = f"dispatch {receipt.status}: {receipt.reason}"
                                self._block_dispatch(task_id, reason)
                                self._reset_task_state(task_id)
                                continue
                        self.reasons.pop(task_id, None)
                    elif self.cycle is not None:
                        cycle_reason = await self.cycle.execute(
                            task_id,
                            can_write=lambda: self._can_write_task(task_id),
                        )
                        if cycle_reason:
                            self.reasons[task_id] = cycle_reason
                            self._reset_task_state(task_id)
                        else:
                            self.reasons.pop(task_id, None)
                    else:
                        records = await cast(LatestSource, self.source).latest(pairs)
                        errors = validate_inputs(self.config, task_id, records)
                        if errors:
                            self.reasons[task_id] = "; ".join(errors)
                            if self._reset_control_state is not None:
                                self._reset_control_state(task_id)
                        else:
                            self.reasons.pop(task_id, None)
                except asyncio.CancelledError:
                    if task_id in self._inflight_dispatch:
                        self._block_dispatch(task_id, "in-flight command cancelled; outcome UNKNOWN")
                    raise
                except Exception as exc:  # noqa: BLE001 - isolate failed control cycles
                    self.reasons[task_id] = f"cycle failed: {exc}"
                    self._reset_task_state(task_id)
                    logger.warning("control task %s skipped: %s", task_id, exc)
        finally:
            if task_id in self._inflight_dispatch:
                self._block_dispatch(task_id, "in-flight command terminated; outcome UNKNOWN")
                self._inflight_dispatch.discard(task_id)
            self.states[task_id] = TaskState.STOPPED
            self._release_authority(task_id)
            self._reset_task_state(task_id)
