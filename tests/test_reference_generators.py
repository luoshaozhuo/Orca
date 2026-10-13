"""内部参考值生成：符号方向、死区、限幅和非法输入。"""
from __future__ import annotations

import pytest

from core.application.reference_generators import (
    InertiaParameters,
    PFRParameters,
    generate_inertia_reference,
    generate_pfr_reference,
)
from core.domain.control_value import ControlValueKind
from core.domain.reference import ReferencePurpose, ReferenceSource


PFR = PFRParameters(50.0, 0.05, 100.0, 6.0, 10.0)
IR = InertiaParameters(20.0, 3.0, 5.0)


def pfr(frequency: float):
    return generate_pfr_reference(
        frequency,
        PFR,
        target="POI",
        timestamp_ns=10,
        valid_until_ns=20,
        generation=1,
    )


def inertia(rocof: float):
    return generate_inertia_reference(
        rocof,
        IR,
        target="POI",
        timestamp_ns=10,
        valid_until_ns=20,
        generation=1,
    )


@pytest.mark.parametrize("frequency", [49.95, 50.0, 50.05])
def test_pfr_deadband(frequency: float) -> None:
    result = pfr(frequency)
    assert result.control.value == pytest.approx(0.0)
    assert result.purpose == ReferencePurpose.PFR_DELTA
    assert result.control.kind == ControlValueKind.DELTA
    assert result.source == ReferenceSource.INTERNAL


def test_pfr_direction_and_asymmetric_limits() -> None:
    assert pfr(49.9).control.value == pytest.approx(5.0)
    assert pfr(50.1).control.value == pytest.approx(-5.0)
    assert pfr(49.0).control.value == pytest.approx(6.0)
    assert pfr(51.0).control.value == pytest.approx(-10.0)


def test_inertia_direction_and_limits() -> None:
    assert inertia(-0.1).control.value == pytest.approx(2.0)
    assert inertia(0.1).control.value == pytest.approx(-2.0)
    assert inertia(-1.0).control.value == pytest.approx(3.0)
    assert inertia(1.0).control.value == pytest.approx(-5.0)
    assert inertia(0.0).purpose == ReferencePurpose.IR_DELTA


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_invalid_measurements_rejected(invalid: float) -> None:
    with pytest.raises(ValueError):
        pfr(invalid)
    with pytest.raises(ValueError):
        inertia(invalid)


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        PFRParameters(50.0, -0.01, 100.0, 6.0, 10.0)
    with pytest.raises(ValueError):
        InertiaParameters(float("nan"), 3.0, 5.0)
