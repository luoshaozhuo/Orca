"""设备 P/Q 联合能力、储能能量和斜率约束测试。"""
from __future__ import annotations

import pytest

from core.domain.device_capability import (
    DevicePQCapability,
    DeviceTechnology,
    StorageEnergyLimits,
)
from core.domain.device_ramp import DeviceRampLimits
from core.domain.power_allocation import DevicePowerCapability


def capability() -> DevicePQCapability:
    return DevicePQCapability(
        device_id="wtg-1",
        technology=DeviceTechnology.WIND,
        p_current=8.0,
        q_current=0.0,
        p_min=0.0,
        p_max=12.0,
        q_min=-10.0,
        q_max=10.0,
        s_max=10.0,
    )


def test_reactive_margin_shrinks_at_high_active_power() -> None:
    q = capability().reactive_bounds()
    assert q.maximum == pytest.approx(6.0)
    assert q.minimum == pytest.approx(-6.0)


def test_active_margin_shrinks_at_high_reactive_power() -> None:
    p = capability().active_bounds(planned_q=6.0)
    assert p.maximum == pytest.approx(8.0)


def test_joint_capability_rejects_impossible_planned_q() -> None:
    with pytest.raises(ValueError, match="invalid reactive"):
        capability().active_bounds(planned_q=11.0)


def test_joint_capability_rejects_infeasible_current() -> None:
    with pytest.raises(ValueError, match="apparent power"):
        DevicePQCapability(
            device_id="bad",
            technology=DeviceTechnology.PV,
            p_current=9.0,
            q_current=9.0,
            p_min=0.0,
            p_max=10.0,
            q_min=-10.0,
            q_max=10.0,
            s_max=10.0,
        )


def test_storage_soc_restricts_discharge_and_charge() -> None:
    original = DevicePowerCapability("ess-1", 0.0, -10.0, 10.0)
    limits = StorageEnergyLimits(
        soc=0.5,
        soc_min=0.2,
        soc_max=0.8,
        energy_mwh=10.0,
        charge_efficiency=0.9,
        discharge_efficiency=0.9,
        horizon_s=3600.0,
    )
    result = limits.apply(original)
    assert result.maximum == pytest.approx(2.7)
    assert result.minimum == pytest.approx(-10.0 / 3.0)


def test_storage_at_minimum_soc_cannot_discharge() -> None:
    limits = StorageEnergyLimits(0.2, 0.2, 0.8, 10, 0.9, 0.9, 3600)
    result = limits.apply(DevicePowerCapability("ess", 0, -10, 10))
    assert result.maximum == 0.0


def test_ramp_limits_bound_single_cycle_change() -> None:
    limits = DeviceRampLimits(up_per_s=2.0, down_per_s=3.0, horizon_s=2.0)
    result = limits.apply(DevicePowerCapability("wtg", 10.0, 0.0, 20.0))
    assert result.minimum == 4.0
    assert result.maximum == 14.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_invalid_ramp_parameter_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        DeviceRampLimits(value, 1.0, 1.0)
