"""设备实时 P/Q 能力及储能能量边界；快照必须来自同一控制周期。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.domain.power_allocation import DevicePowerCapability


class DeviceTechnology(StrEnum):
    WIND = "wind"
    PV = "pv"
    STORAGE = "storage"
    SVG = "svg"


@dataclass(frozen=True, slots=True)
class DevicePQCapability:
    """设备当前 P/Q 矩形边界与视在功率圆约束。"""

    device_id: str
    technology: DeviceTechnology
    p_current: float
    q_current: float
    p_min: float
    p_max: float
    q_min: float
    q_max: float
    s_max: float
    p_auto: bool = True
    q_auto: bool = True
    p_weight: float = 1.0
    q_weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id is required")
        numbers = (
            self.p_current, self.q_current, self.p_min, self.p_max,
            self.q_min, self.q_max, self.s_max,
            self.p_weight, self.q_weight,
        )
        if any(isinstance(v, bool) or not math.isfinite(v) for v in numbers):
            raise ValueError("device P/Q capability must be finite")
        if self.p_weight <= 0 or self.q_weight <= 0:
            raise ValueError("device allocation weights must be positive")
        if self.p_min > self.p_max or self.q_min > self.q_max or self.s_max <= 0:
            raise ValueError("invalid device P/Q limits")
        if self.p_auto and not self.p_min <= self.p_current <= self.p_max:
            raise ValueError("current active power exceeds device limits")
        if self.q_auto and not self.q_min <= self.q_current <= self.q_max:
            raise ValueError("current reactive power exceeds device limits")
        if math.hypot(self.p_current, self.q_current) > self.s_max + 1e-9:
            raise ValueError("current P/Q violates apparent power capability")

    def active_bounds(self, *, planned_q: float | None = None) -> DevicePowerCapability:
        """给定经确认的 Q 或拟执行 Q，计算 P 可行区间。"""
        q = self.q_current if planned_q is None else planned_q
        if not math.isfinite(q) or abs(q) > self.s_max:
            raise ValueError("invalid reactive power for P capability")
        p_limit = math.sqrt(max(0.0, self.s_max ** 2 - q ** 2))
        return DevicePowerCapability(
            device_id=self.device_id,
            current=self.p_current,
            minimum=max(self.p_min, -p_limit),
            maximum=min(self.p_max, p_limit),
            controllable=self.p_auto,
            weight=self.p_weight,
        )

    def reactive_bounds(self, *, planned_p: float | None = None) -> DevicePowerCapability:
        """给定经确认的 P 或拟执行 P，计算 Q 可行区间。"""
        p = self.p_current if planned_p is None else planned_p
        if not math.isfinite(p) or abs(p) > self.s_max:
            raise ValueError("invalid active power for Q capability")
        q_limit = math.sqrt(max(0.0, self.s_max ** 2 - p ** 2))
        return DevicePowerCapability(
            device_id=self.device_id,
            current=self.q_current,
            minimum=max(self.q_min, -q_limit),
            maximum=min(self.q_max, q_limit),
            controllable=self.q_auto,
            weight=self.q_weight,
        )


@dataclass(frozen=True, slots=True)
class StorageEnergyLimits:
    """储能功率符号：放电为正，充电为负；SOC 为 0~1。"""

    soc: float
    soc_min: float
    soc_max: float
    energy_mwh: float
    charge_efficiency: float
    discharge_efficiency: float
    horizon_s: float

    def __post_init__(self) -> None:
        numbers = (
            self.soc, self.soc_min, self.soc_max, self.energy_mwh,
            self.charge_efficiency, self.discharge_efficiency, self.horizon_s,
        )
        if any(isinstance(v, bool) or not math.isfinite(v) for v in numbers):
            raise ValueError("invalid energy limit parameter")
        if not 0 <= self.soc_min <= self.soc <= self.soc_max <= 1:
            raise ValueError("SOC outside operating window")
        if self.energy_mwh <= 0 or self.horizon_s <= 0:
            raise ValueError("energy and horizon must be positive")
        if not 0 < self.charge_efficiency <= 1 or not 0 < self.discharge_efficiency <= 1:
            raise ValueError("efficiencies must be in (0, 1]")

    def apply(self, capability: DevicePowerCapability) -> DevicePowerCapability:
        """根据控制持续时间形成可充/可放功率上下限。"""
        factor = 3600.0 / self.horizon_s
        discharge = (
            (self.soc - self.soc_min) * self.energy_mwh
            * self.discharge_efficiency * factor
        )
        charge = (
            (self.soc_max - self.soc) * self.energy_mwh
            / self.charge_efficiency * factor
        )
        lower = max(capability.minimum, -charge)
        upper = min(capability.maximum, discharge)
        if lower > upper:
            raise ValueError("storage energy bounds are incompatible with device")
        return DevicePowerCapability(
            device_id=capability.device_id,
            current=capability.current,
            minimum=lower,
            maximum=upper,
            controllable=capability.controllable,
            weight=capability.weight,
        )
