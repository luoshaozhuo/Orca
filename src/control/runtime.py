"""Orca 生命周期：服务可运行，任务默认停止；首阶段不下发控制指令。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from core.domain.config import OrcaConfig
from core.domain.control_authority import ControlAuthority, ControlFunction
from core.application.validation import validate_inputs
from .cycle import ControlCycle

logger = logging.getLogger(__name__)


class ControlRuntime:
    """独立任务顺序执行；首阶段仅实现预检查和空控制循环。"""

    def __init__(
        self,
        config: OrcaConfig,
        source: object,
        cycle: ControlCycle | None = None,
        *,
        reset_control_state: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.control_authority = ControlAuthority()
        self.source = source
        self.cycle = cycle
        self._reset_control_state = reset_control_state
        self.write_enabled = False  # 现场写权限未验证；生产装配不得自动放开
        self.states = {name: "STOPPED" for name in config.tasks}
        self.reasons: dict[str, str] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.ready = False
        self._authority: dict[tuple[str, str], str] = {}

    def _function_for(self, task_id: str) -> ControlFunction | None:
        controller = self.config.controllers[self.config.tasks[task_id].controller_id]
        try:
            return ControlFunction(controller.type.upper())
        except ValueError:
            return None

    def _can_write_task(self, task_id: str) -> bool:
        function = self._function_for(task_id)
        return (
            self.write_enabled
            and self.ready
            and function is not None
            and self.control_authority.can_output(function)
            and self.states[task_id] == "RUNNING"
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

    async def start_task(self, task_id: str) -> None:
        if task_id not in self.states:
            raise ValueError(f"unknown task: {task_id}")
        if not self.config.tasks[task_id].enabled:
            raise ValueError(f"disabled task: {task_id}")
        if self.states[task_id] != "STOPPED":
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
        for target in targets:
            self._authority[target] = task_id
        if self._reset_control_state is not None:
            self._reset_control_state(task_id)
        self.states[task_id] = "RUNNING"
        self._workers[task_id] = asyncio.create_task(self._run_task(task_id))

    async def stop_task(self, task_id: str) -> None:
        if task_id not in self.states:
            raise ValueError(f"unknown task: {task_id}")
        worker = self._workers.pop(task_id, None)
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self.states[task_id] = "STOPPED"
        self._release_authority(task_id)
        if self._reset_control_state is not None:
            self._reset_control_state(task_id)

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
                if not self.ready:
                    self.reasons[task_id] = "external dependencies unavailable"
                    if self._reset_control_state is not None:
                        self._reset_control_state(task_id)
                    continue
                try:
                    pairs = [
                        (resource_id, variable.point_id)
                        for resource_id in self.config.tasks[task_id].resource_ids
                        for variable in self.config.resources[resource_id].inputs.values()
                    ]
                    if self.cycle is not None:
                        reason = await self.cycle.execute(
                            task_id,
                            can_write=lambda: self._can_write_task(task_id),
                        )
                        if reason:
                            self.reasons[task_id] = reason
                            if self._reset_control_state is not None:
                                self._reset_control_state(task_id)
                        else:
                            self.reasons.pop(task_id, None)
                    else:
                        records = await self.source.latest(pairs)
                        errors = validate_inputs(self.config, task_id, records)
                        if errors:
                            self.reasons[task_id] = "; ".join(errors)
                            if self._reset_control_state is not None:
                                self._reset_control_state(task_id)
                        else:
                            self.reasons.pop(task_id, None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.reasons[task_id] = f"cycle failed: {exc}"
                    if self._reset_control_state is not None:
                        self._reset_control_state(task_id)
                    logger.warning("control task %s skipped: %s", task_id, exc)
        finally:
            self.states[task_id] = "STOPPED"
            self._release_authority(task_id)
            if self._reset_control_state is not None:
                self._reset_control_state(task_id)
