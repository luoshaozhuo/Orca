"""控制量的阶段、对象、单位及增量合成测试。"""
from __future__ import annotations

import pytest

from core.domain.control_value import (
    ControlQuantity as Q,
    ControlScope as S,
    ControlValue as V,
    ControlValueKind as K,
    ControlValueRole as R,
    combine_power_references,
)


def make(
    quantity: Q = Q.ACTIVE_POWER,
    scope: S = S.POI,
    target: str = "POI",
    kind: K = K.ABSOLUTE,
    value: float = 100.0,
    unit: str = "MW",
) -> V:
    return V(quantity, scope, target, R.REFERENCE, kind, value, unit)


def test_compatible_pfr_and_ir_increments() -> None:
    result = combine_power_references(
        make(), make(kind=K.DELTA, value=4.0),
        make(kind=K.DELTA, value=-2.0),
    )
    assert result.value == pytest.approx(102.0)
    assert result.kind == K.ABSOLUTE


@pytest.mark.parametrize(
    "item",
    [
        (Q.REACTIVE_POWER, S.POI, "POI", K.DELTA, 2.0, "Mvar"),
        (Q.ACTIVE_POWER, S.DEVICE, "wtg001", K.DELTA, 2.0, "MW"),
        (Q.ACTIVE_POWER, S.POI, "POI2", K.DELTA, 2.0, "MW"),
        (Q.ACTIVE_POWER, S.POI, "POI", K.ABSOLUTE, 2.0, "MW"),
    ],
)
def test_incompatible_contributions_rejected(item: tuple) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        combine_power_references(make(), make(*item))


def test_units_cannot_be_interchanged() -> None:
    with pytest.raises(ValueError, match="expected unit"):
        make(Q.VOLTAGE, unit="MW")


def test_control_law_output_and_device_setpoint_distinct() -> None:
    allocation = V(
        Q.ACTIVE_POWER, S.PLANT, "plant",
        R.ALLOCATION_TARGET, K.ABSOLUTE, 80.0, "MW",
    )
    assert allocation.role == R.ALLOCATION_TARGET
    with pytest.raises(ValueError, match="device"):
        V(Q.ACTIVE_POWER, S.PLANT, "plant", R.DEVICE_SETPOINT, K.ABSOLUTE, 2.0, "MW")
    with pytest.raises(ValueError, match="P or Q"):
        V(Q.VOLTAGE, S.PLANT, "plant", R.ALLOCATION_TARGET, K.ABSOLUTE, 110.0, "kV")


def test_nonfinite_values_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        make(value=float("nan"))
