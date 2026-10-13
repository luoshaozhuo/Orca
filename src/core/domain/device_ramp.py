"""设备实时能力进一步受斜率和可执行周期约束。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.power_allocation import DevicePowerCapability


@dataclass(frozen=True, slots=True)
class DeviceRampLimits:
    """单位 MW/s 或 Mvar/s，与目标量一致。"""

    up_per_s: float
    down_per_s: float
    horizon_s: float

    def __post_init__(self) -> None:
        values = (self.up_per_s, self.down_per_s, self.horizon_s)
        if any(isinstance(v, bool) or not math.isfinite(v) for v in values):
            raise ValueError("device ramp parameters must be finite")
        if self.up_per_s < 0 or self.down_per_s < 0 or self.horizon_s <= 0:
            raise ValueError("invalid device ramp parameters")

    def apply(self, capability: DevicePowerCapability) -> DevicePowerCapability:
        """相对当前实测值收紧单周期设备能力区间。"""
        lower = max(
            capability.minimum,
            capability.current - self.down_per_s * self.horizon_s,
        )
        upper = min(
            capability.maximum,
            capability.current + self.up_per_s * self.horizon_s,
        )
        if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
            raise ValueError("ramp limits yield invalid device capability")
        return DevicePowerCapability(
            device_id=capability.device_id,
            current=capability.current,
            minimum=lower,
            maximum=upper,
            controllable=capability.controllable,
            weight=capability.weight,
        )
