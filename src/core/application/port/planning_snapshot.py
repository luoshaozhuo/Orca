"""控制周期输入与分配能力快照的只读边界。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.application.active_power_controller import ActivePowerCycle
from core.domain.capability_snapshot import DeviceCapabilitySnapshot


@dataclass(frozen=True, slots=True)
class PlanningSnapshot:
    """一次控制周期经业务端口冻结的参考、测量与设备能力。"""

    active: ActivePowerCycle
    devices: DeviceCapabilitySnapshot
    acquired_at_ns: int
    expires_at_ns: int | None = None

    def __post_init__(self) -> None:
        if self.acquired_at_ns < 0:
            raise ValueError("planning snapshot timestamp must be nonnegative")
        if self.expires_at_ns is not None and self.expires_at_ns <= self.acquired_at_ns:
            raise ValueError("planning snapshot expiration must follow acquisition")


class PlanningSnapshotPort(Protocol):
    """由基础设施读取并完成测点语义、质量与来源授权检查。"""

    async def acquire(self, task_id: str) -> PlanningSnapshot:
        """采集并校验该任务本周期的一致快照。"""
        ...
