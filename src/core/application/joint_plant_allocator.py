"""双目标设备分配应用服务，唯一输入为冻结后的能力快照。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.application.joint_power_allocator import (
    JointAllocationResult,
    JointPowerAllocator,
)
from core.domain.capability_snapshot import DeviceCapabilitySnapshot
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


@dataclass(frozen=True, slots=True)
class JointPlantTarget:
    """经过场站控制和协调的 P/Q 目标。"""

    active: ControlValue
    reactive: ControlValue

    def __post_init__(self) -> None:
        for value, quantity in (
            (self.active, ControlQuantity.ACTIVE_POWER),
            (self.reactive, ControlQuantity.REACTIVE_POWER),
        ):
            if (
                value.quantity != quantity
                or value.role != ControlValueRole.ALLOCATION_TARGET
                or value.kind != ControlValueKind.ABSOLUTE
                or value.scope not in (ControlScope.POI, ControlScope.PLANT)
            ):
                raise ValueError("joint allocation requires absolute plant P/Q target")
        if (
            self.active.scope != self.reactive.scope
            or self.active.target != self.reactive.target
        ):
            raise ValueError("P/Q targets must refer to the same plant boundary")


@dataclass(slots=True)
class JointPlantAllocator:
    """一次调用中计算两种功率，禁止共享设备能力独立重复分配。"""

    allocator: JointPowerAllocator

    def allocate(
        self,
        target: JointPlantTarget,
        snapshot: DeviceCapabilitySnapshot,
        *,
        now_ns: int,
        maximum_age_ns: int,
    ) -> JointAllocationResult:
        return self.allocator.allocate(
            snapshot,
            p_target=target.active.value,
            q_target=target.reactive.value,
            now_ns=now_ns,
            maximum_age_ns=maximum_age_ns,
        )
