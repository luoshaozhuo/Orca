"""输入质量及新鲜度判断；只有当前周期检查，不依赖看门狗。"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from core.domain.config import OrcaConfig


def validate_inputs(
    config: OrcaConfig,
    task_id: str,
    records: dict[tuple[str, str], dict[str, Any]],
    now: datetime | None = None,
) -> list[str]:
    """返回必需输入不可用的原因；不刷新 Redis 记录的原始采样时间。"""
    clock = now or datetime.now(UTC)
    errors: list[str] = []
    task = config.tasks[task_id]
    for device_id in task.resource_ids:
        resource = config.resources[device_id]
        for name, variable in resource.inputs.items():
            if not variable.required:
                continue
            key = (device_id, variable.point_id)
            record = records.get(key)
            if record is None:
                errors.append(f"{device_id}/{name}: missing")
                continue
            if str(record.get("quality", "")).lower() != "good":
                errors.append(f"{device_id}/{name}: bad quality")
                continue
            try:
                timestamp = datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("naive timestamp")
                age = (clock - timestamp).total_seconds()
                if not math.isfinite(age) or age < -variable.stale_timeout or age > variable.stale_timeout:
                    errors.append(f"{device_id}/{name}: stale (age={age:.3f}s)")
            except (KeyError, TypeError, ValueError):
                errors.append(f"{device_id}/{name}: invalid timestamp")
    return errors
