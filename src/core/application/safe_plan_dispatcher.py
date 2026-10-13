"""执行期最后一次门禁，单条写入，不重发未知结果。"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from uuid import uuid4

from core.application.port.command import CommandPort
from core.application.read_only_planning_cycle import PlanningCycleResult
from core.domain.control_value import ControlQuantity, ControlValueRole
from core.domain.safe_dispatch import (
    DispatchReceipt,
    DispatchStatus,
    OutputBinding,
)

logger = logging.getLogger(__name__)


class SafePlanDispatcher:
    """只负责提交已计算的候选计划，不修改控制算法状态。"""

    def __init__(
        self,
        commander: CommandPort,
        bindings: tuple[OutputBinding, ...],
        *,
        maximum_age_ns: int,
        permitted: Callable[[str], bool],
        generation: Callable[[], int],
        output_owner: Callable[[str, str], str | None],
    ) -> None:
        if maximum_age_ns <= 0:
            raise ValueError("dispatch lifetime must be positive")
        lookup = {(item.device_id, item.quantity): item for item in bindings}
        physical = [(item.device_id, item.point_id) for item in bindings]
        if len(lookup) != len(bindings) or len(set(physical)) != len(physical):
            raise ValueError("duplicate device or physical output binding")
        self._commander = commander
        self._bindings = lookup
        self._maximum_age_ns = maximum_age_ns
        self._permitted = permitted
        self._generation = generation
        self._output_owner = output_owner

    def _reason(
        self,
        task_id: str,
        result: PlanningCycleResult,
        *,
        now_ns: int,
    ) -> str | None:
        if not result.allocation.feasible:
            return "allocation is infeasible"
        if result.allocation.generation != self._generation():
            return "planning generation changed"
        if result.acquired_at_ns > now_ns:
            return "planning snapshot is from the future"
        if now_ns - result.acquired_at_ns > self._maximum_age_ns:
            return "planning snapshot expired"
        if not self._permitted(task_id):
            return "control authority revoked or output disabled"
        return None

    def _commands(
        self,
        task_id: str,
        result: PlanningCycleResult,
    ) -> tuple[tuple[OutputBinding, float], ...]:
        commands: list[tuple[OutputBinding, float]] = []
        points: set[tuple[str, str]] = set()
        for item in (*result.allocation.active, *result.allocation.reactive):
            if item.role != ControlValueRole.DEVICE_SETPOINT:
                raise ValueError("non-setpoint value in dispatch plan")
            if item.quantity not in (
                ControlQuantity.ACTIVE_POWER, ControlQuantity.REACTIVE_POWER
            ):
                raise ValueError("unsupported dispatch quantity")
            binding = self._bindings.get((item.target, item.quantity))
            if binding is None:
                raise ValueError(f"{item.target}/{item.quantity}: output not mapped")
            if not binding.minimum <= item.value <= binding.maximum:
                raise ValueError(f"{item.target}: setpoint outside physical limits")
            point = (binding.device_id, binding.point_id)
            if point in points:
                raise ValueError("duplicate physical output in plan")
            points.add(point)
            if self._output_owner(*point) != task_id:
                raise ValueError(f"{point}: output ownership mismatch")
            commands.append((binding, item.value))
        return tuple(commands)

    async def dispatch(
        self,
        task_id: str,
        result: PlanningCycleResult,
    ) -> DispatchReceipt:
        """预检整个批次，每个命令最多提交一次；部分成功后不补发。"""
        reason = self._reason(task_id, result, now_ns=time.time_ns())
        if reason is not None:
            return DispatchReceipt(DispatchStatus.BLOCKED, reason)
        try:
            commands = self._commands(task_id, result)
        except ValueError as exc:
            return DispatchReceipt(DispatchStatus.BLOCKED, str(exc))
        if not commands:
            return DispatchReceipt(DispatchStatus.BLOCKED, "empty dispatch plan")

        submitted: list[str] = []
        confirmed = 0
        for binding, value in commands:
            reason = self._reason(task_id, result, now_ns=time.time_ns())
            if reason is not None:
                return DispatchReceipt(
                    DispatchStatus.BLOCKED, reason, tuple(submitted), confirmed
                )
            if self._output_owner(binding.device_id, binding.point_id) != task_id:
                return DispatchReceipt(
                    DispatchStatus.BLOCKED, "output ownership changed",
                    tuple(submitted), confirmed,
                )
            command_id = uuid4().hex
            submitted.append(command_id)
            try:
                outcome = await self._commander.write_once(
                    command_id, binding.device_id, binding.point_id, value
                )
            except asyncio.CancelledError:
                logger.warning(
                    "dispatch UNKNOWN cancellation command_id=%s device=%s point=%s",
                    command_id, binding.device_id, binding.point_id,
                )
                return DispatchReceipt(
                    DispatchStatus.UNKNOWN, "Commander write cancelled; outcome unknown",
                    tuple(submitted), confirmed,
                )
            except Exception as exc:
                logger.warning(
                    "dispatch UNKNOWN command_id=%s device=%s point=%s error=%s",
                    command_id, binding.device_id, binding.point_id, exc,
                )
                return DispatchReceipt(
                    DispatchStatus.UNKNOWN, "Commander outcome unknown",
                    tuple(submitted), confirmed,
                )
            if outcome == DispatchStatus.CONFIRMED_SUCCESS:
                confirmed += 1
                continue
            if outcome == DispatchStatus.CONFIRMED_FAILURE:
                return DispatchReceipt(
                    DispatchStatus.CONFIRMED_FAILURE, "Commander rejected command",
                    tuple(submitted), confirmed,
                )
            return DispatchReceipt(
                DispatchStatus.UNKNOWN, "Commander outcome unknown",
                tuple(submitted), confirmed,
            )
        return DispatchReceipt(
            DispatchStatus.CONFIRMED_SUCCESS, "all commands confirmed",
            tuple(submitted), confirmed,
        )
