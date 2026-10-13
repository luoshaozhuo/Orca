"""冻结设备能力快照；同一版本内 P/Q、SOC、旁路及爬坡信息一致。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.device_capability import (
    DevicePQCapability,
    DeviceTechnology,
    StorageEnergyLimits,
)
from core.domain.device_ramp import DeviceRampLimits
from core.domain.power_allocation import DevicePowerCapability


@dataclass(frozen=True, slots=True)
class DeviceCapabilityRecord:
    """一次设备能力观测以及可选的单周期安全约束。"""

    capability: DevicePQCapability
    timestamp_ns: int
    generation: int
    p_ramp: DeviceRampLimits | None = None
    q_ramp: DeviceRampLimits | None = None
    storage: StorageEnergyLimits | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0 or self.generation < 0:
            raise ValueError("invalid device capability timestamp or generation")
        if self.storage is not None and self.capability.technology != DeviceTechnology.STORAGE:
            raise ValueError("storage constraints require a storage device")


@dataclass(frozen=True, slots=True)
class DeviceCapabilitySnapshot:
    """不可变的单一代际快照；过期、未来采样和混代一律拒绝。"""

    records: tuple[DeviceCapabilityRecord, ...]
    generation: int
    sampled_at_ns: int
    valid_until_ns: int

    def __post_init__(self) -> None:
        if self.generation < 0 or not 0 <= self.sampled_at_ns < self.valid_until_ns:
            raise ValueError("invalid snapshot generation or validity")
        names = [record.capability.device_id for record in self.records]
        if len(names) != len(set(names)):
            raise ValueError("duplicate device in capability snapshot")
        for record in self.records:
            if record.generation != self.generation:
                raise ValueError("mixed capability generations")
            if record.timestamp_ns > self.sampled_at_ns:
                raise ValueError("future device observation in snapshot")
            if record.timestamp_ns < 0:
                raise ValueError("invalid device observation")

    def validate(self, *, now_ns: int, maximum_age_ns: int) -> None:
        if maximum_age_ns <= 0:
            raise ValueError("maximum capability age must be positive")
        if not self.sampled_at_ns <= now_ns < self.valid_until_ns:
            raise ValueError("capability snapshot is unavailable or expired")
        if now_ns - self.sampled_at_ns > maximum_age_ns:
            raise ValueError("capability snapshot is stale")
        for record in self.records:
            if now_ns - record.timestamp_ns > maximum_age_ns:
                raise ValueError(
                    f"{record.capability.device_id}: capability observation is stale"
                )

    def bounded(self) -> tuple[DevicePQCapability, ...]:
        """计算单周期可调边界；未获得完整数据不得推断能力。"""
        from dataclasses import replace

        result: list[DevicePQCapability] = []
        for record in self.records:
            item = record.capability
            p = DevicePowerCapability(
                item.device_id, item.p_current, item.p_min, item.p_max,
                item.p_auto,
            )
            q = DevicePowerCapability(
                item.device_id, item.q_current, item.q_min, item.q_max,
                item.q_auto,
            )
            if record.storage is not None:
                p = record.storage.apply(p)
            if record.p_ramp is not None:
                p = record.p_ramp.apply(p)
            if record.q_ramp is not None:
                q = record.q_ramp.apply(q)
            if p.minimum > p.maximum or q.minimum > q.maximum:
                raise ValueError(f"{item.device_id}: infeasible capability limits")
            if p.controllable and not p.minimum <= p.current <= p.maximum:
                raise ValueError(f"{item.device_id}: current P outside capability")
            if q.controllable and not q.minimum <= q.current <= q.maximum:
                raise ValueError(f"{item.device_id}: current Q outside capability")
            result.append(
                replace(
                    item,
                    p_min=p.minimum, p_max=p.maximum,
                    q_min=q.minimum, q_max=q.maximum,
                )
            )
        return tuple(result)
