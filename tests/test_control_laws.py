"""控制律输出、积分状态、限幅与轨迹边界测试。"""
from __future__ import annotations

import pytest

from core.application.control_laws import (
    DirectControlLaw,
    PIControlLaw,
    ProportionalControlLaw,
)
from core.application.control_trajectory import RampTrajectory
from core.domain.control_law import ControlLawInput
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


def control_value(value: float, role: ControlValueRole) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER,
        ControlScope.POI,
        "POI",
        role,
        ControlValueKind.ABSOLUTE,
        value,
        "MW",
    )


def sample(
    reference: float = 100.0,
    measurement: float = 90.0,
    *,
    achievable: float | None = None,
) -> ControlLawInput:
    return ControlLawInput(
        reference=control_value(reference, ControlValueRole.REFERENCE),
        measurement=control_value(measurement, ControlValueRole.MEASUREMENT),
        dt_s=1.0,
        minimum=0.0,
        maximum=110.0,
        feedforward=90.0,
        achievable=achievable,
    )


def test_direct_control_is_clipped_and_does_not_write() -> None:
    output = DirectControlLaw().calculate(sample(reference=120.0))
    assert output.candidate.value == 110.0
    assert output.saturated
    assert output.error == 30.0


def test_proportional_control_uses_feedforward() -> None:
    output = ProportionalControlLaw(kp=0.5).calculate(sample())
    assert output.candidate.value == pytest.approx(95.0)


def test_pi_integrates_previous_cycle_error() -> None:
    law = PIControlLaw(kp=0.5, ki=0.2)
    assert law.calculate(sample()).candidate.value == pytest.approx(95.0)
    assert law.calculate(sample()).candidate.value == pytest.approx(97.0)
    law.reset()
    assert law.calculate(sample()).candidate.value == pytest.approx(95.0)


def test_pi_does_not_accumulate_while_clipped_outward() -> None:
    law = PIControlLaw(kp=1.0, ki=10.0)
    current = sample(reference=200.0, measurement=0.0)
    for _ in range(20):
        assert law.calculate(current).candidate.value == 110.0
    assert law._integral == pytest.approx(0.0)


def test_ramp_requires_known_initial_state() -> None:
    trajectory = RampTrajectory(up_per_s=2.0, down_per_s=4.0)
    with pytest.raises(RuntimeError, match="initialized"):
        trajectory.step(100.0, 1.0)
    trajectory.initialize(50.0)
    assert trajectory.step(100.0, 2.0) == pytest.approx(54.0)
    assert trajectory.step(0.0, 1.0) == pytest.approx(50.0)
    trajectory.reset()
    with pytest.raises(RuntimeError, match="initialized"):
        trajectory.step(10.0, 1.0)


def test_mismatched_reference_and_feedback_are_rejected() -> None:
    invalid = ControlValue(
        ControlQuantity.REACTIVE_POWER,
        ControlScope.POI,
        "POI",
        ControlValueRole.MEASUREMENT,
        ControlValueKind.ABSOLUTE,
        1.0,
        "Mvar",
    )
    with pytest.raises(ValueError, match="same semantics"):
        ControlLawInput(
            reference=control_value(10.0, ControlValueRole.REFERENCE),
            measurement=invalid,
            dt_s=1.0,
            minimum=0.0,
            maximum=100.0,
        )
