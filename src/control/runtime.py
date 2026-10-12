"""Orca 生命周期：服务可运行，任务默认停止；首阶段不下发控制指令。"""
from __future__ import annotations

import asyncio
import logging

from core.domain.config import OrcaConfig
from core.application.validation import validate_inputs
from .cycle import ControlCycle

logger = logging.getLogger(__name__)


class ControlRuntime:
    """独立任务顺序执行；首阶段仅实现预检查和空控制循环。"""

    def __init__(self, config: OrcaConfig, source: object, cycle: ControlCycle | None = None) -> None:
        self.config = config
        self.source = source
        self.cycle = cycle
        self.write_enabled = False
        self.states = {name: "STOPPED" for name in config.tasks}
        self.reasons: dict[str, str] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.ready = False

    async def start_task(self, task_id: str) -> None:
        if task_id not in self.states:
            raise ValueError(f"unknown task: {task_id}")
        if not self.config.tasks[task_id].enabled:
            raise ValueError(f"disabled task: {task_id}")
        if self.states[task_id] != "STOPPED":
            return
        if not self.ready:
            raise RuntimeError("dependencies or point mappings are not verified")
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

    async def stop(self) -> None:
        for task_id in list(self.states):
            await self.stop_task(task_id)

    async def _run_task(self, task_id: str) -> None:
        """周期执行不重叠、不补算；无输出，不调用任何控制策略。"""
        interval = self.config.tasks[task_id].interval
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            while True:
                await asyncio.sleep(max(0.0, deadline - loop.time()))
                deadline = loop.time() + interval
                if not self.ready:
                    self.reasons[task_id] = "external dependencies unavailable"
                    continue
                try:
                    pairs = [
                        (resource_id, variable.point_id)
                        for resource_id in self.config.tasks[task_id].resource_ids
                        for variable in self.config.resources[resource_id].inputs.values()
                    ]
                    if self.cycle is not None:
                        reason = await self.cycle.execute(task_id, allow_write=self.write_enabled)
                        if reason:
                            self.reasons[task_id] = reason
                        else:
                            self.reasons.pop(task_id, None)
                    else:
                        records = await self.source.latest(pairs)
                        errors = validate_inputs(self.config, task_id, records)
                        if errors:
                            self.reasons[task_id] = "; ".join(errors)
                        else:
                            self.reasons.pop(task_id, None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.reasons[task_id] = f"cycle failed: {exc}"
                    logger.warning("control task %s skipped: %s", task_id, exc)
        finally:
            self.states[task_id] = "STOPPED"
