"""控制决策校验与测点映射。"""
from __future__ import annotations

import math

from core.domain.config import OrcaConfig
from core.domain.decision import ControlDecision


def validate_decision(config: OrcaConfig, task_id: str, decision: ControlDecision) -> list[str]:
    """拒绝未授权资源、未配置变量、重复目标和非有限值。"""
    task = config.tasks[task_id]
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in decision.outputs:
        key = (item.device_id, item.variable)
        if key in seen:
            errors.append(f"duplicate output: {key}")
        seen.add(key)
        if item.device_id not in task.resource_ids:
            errors.append(f"device outside task: {item.device_id}")
            continue
        if item.variable not in config.resources[item.device_id].outputs:
            errors.append(f"unknown output: {item.device_id}/{item.variable}")
        if not math.isfinite(item.value):
            errors.append(f"non-finite value: {item.device_id}/{item.variable}")
    return errors
