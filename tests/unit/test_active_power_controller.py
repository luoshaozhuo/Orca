"""场站有功计算服务的端到端纯计算测试。"""
from __future__ import annotations

import pytest

from core.application.active_power_controller import (
    ActivePowerController,
    ActivePowerCycle,
)
from core.application.control_coordinator import ControlCoordinator
from core.application.control_laws import DirectControlLaw, PIControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_trajectory import PassthroughTrajectory
from core.domain.config import CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import ControlDemand, CoordinationFunction


def power(value: float, role: ControlValueRole, kind: ControlValueKind) -> ControlValue:
    return ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target="POI",
        role=role,
        kind=kind,
        value=value,
        unit="MW",
    )


def controller(*, pi: bool = False) -> ActivePowerController:
    pipeline = ControlPipeline(
        trajectory=PassthroughTrajectory(),
        law=PIControlLaw(kp=0.5, ki=1.0) if pi else DirectControlLaw(),
    )
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "sum",
            "parameters": {"weights": {"pfr": 1.0, "ir": 1.0}},
        }
    )
    return ActivePowerController(pipeline, ControlCoordinator(policy))


def cycle(
    reference: float = 90.0,
    measurement: float = 80.0,
    demands: tuple[ControlDemand, ...] = (),
    maximum: float = 120.0,
) -> ActivePowerCycle:
    return ActivePowerCycle(
        base=power(reference, ControlValueRole.REFERENCE, ControlValueKind.ABSOLUTE),
        measurement=power(
            measurement,
            ControlValueRole.MEASUREMENT,
            ControlValueKind.ABSOLUTE,
        ),
        dt_s=1.0,
        minimum=0.0,
        maximum=maximum,
        demands=demands,
        feedforward=90.0,
    )


def demand(function: CoordinationFunction, amount: float) -> ControlDemand:
    return ControlDemand(
        function=function,
        value=power(
            amount, ControlValueRole.CONTROL_DEMAND, ControlValueKind.DELTA
        ),
    )


def test_base_plus_multiple_fast_support_demands() -> None:
    result = controller().evaluate(
        cycle(
            demands=(
                demand(CoordinationFunction.PFR, 5.0),
                demand(CoordinationFunction.IR, -2.0),
            )
        )
    )
    assert result.control.candidate.value == pytest.approx(90.0)
    assert result.coordination.target.value == pytest.approx(93.0)
    assert result.coordination.dp_coord == pytest.approx(3.0)
    assert len(result.coordination.contributions) == 2


def test_coordination_beyond_cycle_limit_fails_not_silently_clips() -> None:
    service = controller()
    with pytest.raises(ValueError, match="exceeds cycle limits"):
        service.evaluate(cycle(demands=(demand(CoordinationFunction.PFR, 50.0),)))


def test_coordination_failure_clears_pi_state() -> None:
    service = controller(pi=True)
    first = service.evaluate(cycle()).control.candidate.value
    assert service.evaluate(cycle()).control.candidate.value > first
    with pytest.raises(ValueError, match="exceeds cycle limits"):
        service.evaluate(
            cycle(demands=(demand(CoordinationFunction.IR, 60.0),))
        )
    assert service.evaluate(cycle()).control.candidate.value == pytest.approx(first)


def test_mismatched_demand_scope_rejected() -> None:
    bad = ControlDemand(
        function=CoordinationFunction.PFR,
        value=ControlValue(
            quantity=ControlQuantity.ACTIVE_POWER,
            scope=ControlScope.PLANT,
            target="POI",
            role=ControlValueRole.CONTROL_DEMAND,
            kind=ControlValueKind.DELTA,
            value=5.0,
            unit="MW",
        ),
    )
    with pytest.raises(ValueError, match="incompatible power semantics"):
        controller().evaluate(cycle(demands=(bad,)))
