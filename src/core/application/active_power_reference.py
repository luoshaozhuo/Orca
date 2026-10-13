"""将经过来源仲裁的有功参考转换为单周期控制输入。"""
from __future__ import annotations

from dataclasses import replace

from core.application.active_power_controller import ActivePowerCycle
from core.application.reference_manager import ReferenceManager
from core.domain.control_value import ControlValue, ControlValueRole
from core.domain.coordination import ControlDemand, CoordinationFunction
from core.domain.reference import ReferencePurpose, ReferenceSource


def active_power_cycle_from_references(
    references: ReferenceManager,
    *,
    target: str,
    measurement: ControlValue,
    now_ns: int,
    authorized_sources: frozenset[ReferenceSource],
    dt_s: float,
    minimum: float,
    maximum: float,
    feedforward: float = 0.0,
    achievable: float | None = None,
    active_functions: frozenset[CoordinationFunction] = frozenset(),
    required_generation: int | None = None,
    base_purpose: ReferencePurpose = ReferencePurpose.ACTIVE_BASE,
) -> ActivePowerCycle:
    """基于同一时刻的有效参考建立快照；缺失已启用功能立即闭锁。"""
    if base_purpose not in (ReferencePurpose.ACTIVE_BASE, ReferencePurpose.ACTIVE_LIMIT):
        raise ValueError("active base reference must be a setpoint or a limit")
    base = references.current(
        base_purpose,
        target,
        now_ns=now_ns,
        authorized_sources=authorized_sources,
    )
    if base is None:
        raise ValueError(f"{base_purpose.value}: reference unavailable or unauthorized")
    if required_generation is not None and base.generation != required_generation:
        raise ValueError(f"{base_purpose.value}: reference generation mismatch")

    mappings = {
        CoordinationFunction.PFR: ReferencePurpose.PFR_DELTA,
        CoordinationFunction.IR: ReferencePurpose.IR_DELTA,
    }
    if active_functions - set(mappings):
        raise ValueError("unsupported active coordination function")
    demands: list[ControlDemand] = []
    for function in sorted(active_functions, key=lambda item: item.value):
        sample = references.current(
            mappings[function],
            target,
            now_ns=now_ns,
            authorized_sources=authorized_sources,
        )
        if sample is None:
            raise ValueError(f"{function.value} reference unavailable or unauthorized")
        if required_generation is not None and sample.generation != required_generation:
            raise ValueError(f"{function.value} reference generation mismatch")
        demands.append(
            ControlDemand(
                function=function,
                value=replace(sample.control, role=ControlValueRole.CONTROL_DEMAND),
            )
        )
    return ActivePowerCycle(
        base=base.control,
        measurement=measurement,
        dt_s=dt_s,
        minimum=minimum,
        maximum=maximum,
        demands=tuple(demands),
        feedforward=feedforward,
        achievable=achievable,
    )
