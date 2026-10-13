"""场站无功控制计算编排：AVC 三模式到无功候选目标。

定无功与定功率因数模式复用无功管线；定电压模式使用独立的
电压-无功控制律。功率因数目标先映射为无功参考再进入同一管线，
有功接近零时判定不适定并拒绝计算。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from core.application.control_laws import VoltageReactiveLaw
from core.application.control_pipeline import ControlPipeline
from core.domain.avc import AvcMode
from core.domain.control_law import ControlLawInput
from core.domain.control_value import (
    ControlQuantity,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)

# 功率因数映射的最小有功幅值：低于该值时 tan(acos(pf)) 映射不适定。
_PF_MIN_ACTIVE_MW = 1e-3


@dataclass(frozen=True, slots=True)
class ReactivePowerCycle:
    """同一周期的无功控制输入快照；不访问外部服务。"""

    mode: AvcMode
    reference: ControlValue
    q_meas: ControlValue
    dt_s: float
    minimum: float
    maximum: float
    u_meas: ControlValue | None = None
    p_meas: ControlValue | None = None

    def __post_init__(self) -> None:
        if self.reference.role != ControlValueRole.REFERENCE:
            raise ValueError("reactive control requires a reference input")
        if self.q_meas.role != ControlValueRole.MEASUREMENT:
            raise ValueError("reactive control requires a measurement input")
        if self.q_meas.quantity != ControlQuantity.REACTIVE_POWER:
            raise ValueError("reactive measurement must be reactive power")
        expected = {
            AvcMode.REACTIVE_POWER: ControlQuantity.REACTIVE_POWER,
            AvcMode.VOLTAGE: ControlQuantity.VOLTAGE,
            AvcMode.POWER_FACTOR: ControlQuantity.POWER_FACTOR,
        }[self.mode]
        if self.reference.quantity != expected:
            raise ValueError(f"{self.mode}: reference must be {expected}")
        if self.reference.kind != ControlValueKind.ABSOLUTE:
            raise ValueError("reactive reference must be absolute")
        if self.mode == AvcMode.VOLTAGE:
            if self.u_meas is None or self.u_meas.quantity != ControlQuantity.VOLTAGE:
                raise ValueError("VOLTAGE mode requires a voltage measurement")
            if (
                self.u_meas.target != self.reference.target
                or self.u_meas.unit != self.reference.unit
            ):
                raise ValueError("voltage reference and measurement must share semantics")
        if self.mode == AvcMode.POWER_FACTOR and (
            self.p_meas is None or self.p_meas.quantity != ControlQuantity.ACTIVE_POWER
        ):
            raise ValueError("POWER_FACTOR mode requires an active power measurement")
        values = (self.dt_s, self.minimum, self.maximum)
        if any(not math.isfinite(item) for item in values):
            raise ValueError("reactive cycle limits must be finite")
        if self.dt_s <= 0 or self.minimum > self.maximum:
            raise ValueError("invalid reactive cycle sample period or limits")


def power_factor_to_reactive(pf_ref: float, p_meas: float) -> float:
    """功率因数目标映射为无功参考（Mvar）。

    符号约定：pf_ref > 0 为滞后（向电网注入正无功），pf_ref < 0 为超前；
    无功符号随有功方向自然翻转，保持同一功率因数角。
    """
    if not 0 < abs(pf_ref) <= 1:
        raise ValueError("power factor reference must be in [-1, 0) ∪ (0, 1]")
    if abs(p_meas) < _PF_MIN_ACTIVE_MW:
        raise ValueError("power factor mapping is ill-conditioned near zero active power")
    q_magnitude = abs(p_meas) * math.tan(math.acos(abs(pf_ref)))
    direction = math.copysign(1.0, pf_ref) * math.copysign(1.0, p_meas)
    return direction * q_magnitude


@dataclass(slots=True)
class ReactivePowerController:
    """按模式计算并网点无功候选目标；不产生设备指令。"""

    pipeline: ControlPipeline
    mode: AvcMode
    voltage_law: VoltageReactiveLaw | None = None

    def __post_init__(self) -> None:
        if self.mode == AvcMode.VOLTAGE and self.voltage_law is None:
            raise ValueError("VOLTAGE mode requires a voltage control law")

    def evaluate(self, cycle: ReactivePowerCycle) -> ControlValue:
        """输出限幅后的无功候选目标；异常时废弃有状态算法历史。"""
        if cycle.mode != self.mode:
            raise ValueError("reactive cycle mode does not match controller")
        try:
            if self.mode == AvcMode.VOLTAGE:
                assert self.voltage_law is not None
                assert cycle.u_meas is not None
                value = self.voltage_law.calculate(
                    cycle.reference.value,
                    cycle.u_meas.value,
                    cycle.q_meas.value,
                    cycle.dt_s,
                    cycle.minimum,
                    cycle.maximum,
                )
                return ControlValue(
                    quantity=ControlQuantity.REACTIVE_POWER,
                    scope=cycle.q_meas.scope,
                    target=cycle.q_meas.target,
                    role=ControlValueRole.CANDIDATE_TARGET,
                    kind=ControlValueKind.ABSOLUTE,
                    value=value,
                    unit="Mvar",
                )
            q_ref = cycle.reference.value
            if self.mode == AvcMode.POWER_FACTOR:
                assert cycle.p_meas is not None
                q_ref = power_factor_to_reactive(q_ref, cycle.p_meas.value)
            reference = replace(
                cycle.reference,
                quantity=ControlQuantity.REACTIVE_POWER,
                scope=cycle.q_meas.scope,
                target=cycle.q_meas.target,
                value=q_ref,
                unit="Mvar",
            )
            result = self.pipeline.evaluate(
                ControlLawInput(
                    reference=reference,
                    measurement=cycle.q_meas,
                    dt_s=cycle.dt_s,
                    minimum=cycle.minimum,
                    maximum=cycle.maximum,
                )
            )
            return result.candidate
        except Exception:
            self.reset()
            raise

    def reset(self) -> None:
        """功能退出或输入失效时清除算法状态。"""
        self.pipeline.reset()
        if self.voltage_law is not None:
            self.voltage_law.reset()
