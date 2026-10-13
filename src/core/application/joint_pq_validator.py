"""同一设备的 P/Q 指令必须在共同视在功率可行域内。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.control_value import ControlQuantity, ControlValue, ControlValueRole
from core.domain.device_capability import DevicePQCapability


@dataclass(frozen=True, slots=True)
class JointPQPlan:
    """经过联立校验的设备候选 P/Q 指令，仍不得直接下发。"""

    active: tuple[ControlValue, ...]
    reactive: tuple[ControlValue, ...]


class JointPQValidator:
    """合并两个独立计算结果，再做设备级联合能力校验。"""

    def validate(
        self,
        devices: tuple[DevicePQCapability, ...],
        active: tuple[ControlValue, ...],
        reactive: tuple[ControlValue, ...],
    ) -> JointPQPlan:
        by_id = {item.device_id: item for item in devices}
        if len(by_id) != len(devices):
            raise ValueError("duplicate P/Q device capability")
        p_targets = self._targets(active, ControlQuantity.ACTIVE_POWER)
        q_targets = self._targets(reactive, ControlQuantity.REACTIVE_POWER)
        if (set(p_targets) | set(q_targets)) - set(by_id):
            raise ValueError("P/Q plan references unknown device")

        for device_id, capability in by_id.items():
            p = p_targets.get(device_id, capability.p_current)
            q = q_targets.get(device_id, capability.q_current)
            if device_id in p_targets and not capability.p_auto:
                raise ValueError(f"{device_id}: active power is not auto-controlled")
            if device_id in q_targets and not capability.q_auto:
                raise ValueError(f"{device_id}: reactive power is not auto-controlled")
            if not capability.p_min <= p <= capability.p_max:
                raise ValueError(f"{device_id}: active power violates device capability")
            if not capability.q_min <= q <= capability.q_max:
                raise ValueError(f"{device_id}: reactive power violates device capability")
            if math.hypot(p, q) > capability.s_max + 1e-9:
                raise ValueError(f"{device_id}: P/Q exceeds apparent power capability")
        return JointPQPlan(active=active, reactive=reactive)

    @staticmethod
    def _targets(
        items: tuple[ControlValue, ...],
        quantity: ControlQuantity,
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for item in items:
            if item.role != ControlValueRole.DEVICE_SETPOINT or item.quantity != quantity:
                raise ValueError("invalid joint P/Q device setpoint")
            if item.target in values:
                raise ValueError("duplicate device setpoint")
            values[item.target] = item.value
        return values
