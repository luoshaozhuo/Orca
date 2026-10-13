"""内部一次调频和惯量响应参考值生成。

只计算参考增量，不执行场站闭环、设备分配或指令下发。
参数必须来自项目整定值；此处不固化标准性能指标。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.reference import (
    ControlReference,
    ReferencePurpose,
    ReferenceSource,
)


@dataclass(frozen=True, slots=True)
class PFRParameters:
    nominal_frequency_hz: float
    deadband_hz: float
    gain_mw_per_hz: float
    max_increase_mw: float
    max_decrease_mw: float

    def __post_init__(self) -> None:
        values = (
            self.nominal_frequency_hz,
            self.deadband_hz,
            self.gain_mw_per_hz,
            self.max_increase_mw,
            self.max_decrease_mw,
        )
        if any(not math.isfinite(item) for item in values):
            raise ValueError("PFR parameters must be finite")
        if self.nominal_frequency_hz <= 0 or any(item < 0 for item in values[1:]):
            raise ValueError("PFR gains and limits must be nonnegative")


@dataclass(frozen=True, slots=True)
class InertiaParameters:
    gain_mw_second_per_hz: float
    max_increase_mw: float
    max_decrease_mw: float

    def __post_init__(self) -> None:
        values = (
            self.gain_mw_second_per_hz,
            self.max_increase_mw,
            self.max_decrease_mw,
        )
        if any(not math.isfinite(item) or item < 0 for item in values):
            raise ValueError("inertia parameters must be finite and nonnegative")


def _clamp(value: float, down: float, up: float) -> float:
    return max(-down, min(up, value))


def _reference(
    purpose: ReferencePurpose,
    target: str,
    value: float,
    timestamp_ns: int,
    valid_until_ns: int,
    generation: int,
    event_id: str | None = None,
) -> ControlReference:
    control = ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target=target,
        role=ControlValueRole.REFERENCE,
        kind=ControlValueKind.DELTA,
        value=value,
        unit="MW",
    )
    return ControlReference(
        purpose=purpose,
        source=ReferenceSource.INTERNAL,
        control=control,
        timestamp_ns=timestamp_ns,
        valid_until_ns=valid_until_ns,
        generation=generation,
        sequence_id=timestamp_ns,
        event_id=event_id,
    )


def delta_reference(
    purpose: ReferencePurpose,
    value: float,
    *,
    target: str,
    timestamp_ns: int,
    valid_until_ns: int,
    generation: int,
    event_id: str | None = None,
) -> ControlReference:
    """事件状态机已确定的功率增量参考；数值必须有限且属于增量用途。"""
    if purpose not in (ReferencePurpose.PFR_DELTA, ReferencePurpose.IR_DELTA):
        raise ValueError("delta reference requires a delta purpose")
    if not math.isfinite(value):
        raise ValueError("delta reference value must be finite")
    return _reference(
        purpose, target, value, timestamp_ns, valid_until_ns, generation, event_id,
    )


def generate_pfr_reference(
    frequency_hz: float,
    parameters: PFRParameters,
    *,
    target: str,
    timestamp_ns: int,
    valid_until_ns: int,
    generation: int,
) -> ControlReference:
    """正频差降有功，负频差升有功；死区内不响应。"""
    if not math.isfinite(frequency_hz):
        raise ValueError("frequency must be finite")
    deviation = frequency_hz - parameters.nominal_frequency_hz
    magnitude = max(abs(deviation) - parameters.deadband_hz, 0.0)
    change = -math.copysign(parameters.gain_mw_per_hz * magnitude, deviation)
    return _reference(
        ReferencePurpose.PFR_DELTA,
        target,
        _clamp(
            change,
            parameters.max_decrease_mw,
            parameters.max_increase_mw,
        ),
        timestamp_ns,
        valid_until_ns,
        generation,
    )


def generate_inertia_reference(
    rocof_hz_per_second: float,
    parameters: InertiaParameters,
    *,
    target: str,
    timestamp_ns: int,
    valid_until_ns: int,
    generation: int,
) -> ControlReference:
    """基于已滤波、已验证的频率变化率生成候选功率增量。"""
    if not math.isfinite(rocof_hz_per_second):
        raise ValueError("RoCoF must be finite")
    change = -parameters.gain_mw_second_per_hz * rocof_hz_per_second
    return _reference(
        ReferencePurpose.IR_DELTA,
        target,
        _clamp(
            change,
            parameters.max_decrease_mw,
            parameters.max_increase_mw,
        ),
        timestamp_ns,
        valid_until_ns,
        generation,
    )
