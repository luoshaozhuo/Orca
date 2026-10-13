"""场站级控制管线组合与周期状态测试。"""
from __future__ import annotations

import pytest

from core.application.control_laws import PIControlLaw, ProportionalControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_signal import LowPassSignal, PassthroughSignal
from core.application.control_trajectory import PassthroughTrajectory, RampTrajectory
from core.domain.control_law import ControlLawInput
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


def point(amount: float, role: ControlValueRole) -> ControlValue:
    return ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target="POI",
        role=role,
        kind=ControlValueKind.ABSOLUTE,
        value=amount,
        unit="MW",
    )


def sample(reference: float, measurement: float) -> ControlLawInput:
    return ControlLawInput(
        reference=point(reference, ControlValueRole.REFERENCE),
        measurement=point(measurement, ControlValueRole.MEASUREMENT),
        dt_s=1.0,
        minimum=0.0,
        maximum=200.0,
        feedforward=100.0,
    )


def test_filtered_feedback_and_reference_ramp_are_independent() -> None:
    signal = LowPassSignal(tau_s=1.0)
    trajectory = RampTrajectory(up_per_s=2.0, down_per_s=3.0)
    trajectory.initialize(100.0)
    pipeline = ControlPipeline(
        trajectory=trajectory,
        law=ProportionalControlLaw(kp=1.0),
        signal=signal,
    )
    first = pipeline.evaluate(sample(110.0, 90.0))
    assert first.candidate.value == pytest.approx(112.0)
    second = pipeline.evaluate(sample(110.0, 110.0))
    assert second.candidate.value == pytest.approx(104.0)
    pipeline.reset()
    with pytest.raises(RuntimeError, match="initialized"):
        pipeline.evaluate(sample(110.0, 90.0))


def test_pipeline_reset_clears_pi_and_filter() -> None:
    law = PIControlLaw(kp=0.5, ki=1.0)
    pipeline = ControlPipeline(
        trajectory=PassthroughTrajectory(),
        law=law,
        signal=PassthroughSignal(),
    )
    first = pipeline.evaluate(sample(100.0, 90.0)).candidate.value
    assert pipeline.evaluate(sample(100.0, 90.0)).candidate.value > first
    pipeline.reset()
    assert pipeline.evaluate(sample(100.0, 90.0)).candidate.value == pytest.approx(first)


def test_low_pass_rejects_bad_timing() -> None:
    signal = LowPassSignal(tau_s=2.0)
    with pytest.raises(ValueError, match="period"):
        signal.step(50.0, 0.0)
