"""一次控制周期：输入验证、策略、决策校验、单次指令提交。"""
from __future__ import annotations

import logging
import math
from uuid import uuid4

from core.application.port.command import CommandPort
from core.application.port.strategy import ControlStrategy
from core.application.validation import validate_inputs
from core.domain.config import OrcaConfig

from .decision import validate_decision

logger = logging.getLogger(__name__)


class ControlCycle:
    """一次执行；不重试、不缓存指令、不补发。"""

    def __init__(
        self,
        config: OrcaConfig,
        source: object,
        commander: CommandPort,
        strategy: ControlStrategy,
    ) -> None:
        self.config = config
        self.source = source
        self.commander = commander
        self.strategy = strategy

    async def execute(self, task_id: str, *, allow_write: bool = False) -> str | None:
        """失败时返回原因；任何前置条件未满足均不得写设备。"""
        task = self.config.tasks[task_id]
        pairs = [
            (device_id, variable.point_id)
            for device_id in task.resource_ids
            for variable in self.config.resources[device_id].inputs.values()
        ]
        records = await self.source.latest(pairs)
        errors = validate_inputs(self.config, task_id, records)
        if errors:
            return "; ".join(errors)
        measurements: dict[str, dict[str, float]] = {}
        for device_id in task.resource_ids:
            values: dict[str, float] = {}
            for name, variable in self.config.resources[device_id].inputs.items():
                record = records.get((device_id, variable.point_id))
                if record is None:
                    continue
                value = record["value"]
                if isinstance(value, bool) or not isinstance(value, (float, int)):
                    return f"{device_id}/{name}: numeric value required"
                if not math.isfinite(float(value)):
                    return f"{device_id}/{name}: non-finite input"
                values[name] = float(value)
            measurements[device_id] = values
        decision = self.strategy.solve(measurements)
        errors = validate_decision(self.config, task_id, decision)
        if errors:
            return "; ".join(errors)
        if decision.outputs and not allow_write:
            return "output disabled: field command permissions are unverified"
        # 完成决策全量校验后才可能发生第一次设备写入。
        for item in decision.outputs:
            point_id = self.config.resources[item.device_id].outputs[item.variable].point_id
            command_id = uuid4().hex
            try:
                outcome = await self.commander.write_once(
                    command_id, item.device_id, point_id, item.value
                )
            except Exception as exc:
                # 包括超时：执行结果可能未知，不再发送同一命令。
                logger.warning("command UNKNOWN id=%s target=%s/%s error=%s",
                               command_id, item.device_id, point_id, exc)
                return f"command UNKNOWN: {command_id}"
            if outcome != "CONFIRMED_SUCCESS":
                return f"command {outcome}: {command_id}"
        return None
