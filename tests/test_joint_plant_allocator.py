"""场站同边界 P/Q 目标与冻结能力快照的组合测试。"""
from __future__ import annotations

import pytest

from core.application.joint_plant_allocator import JointPlantAllocator, JointPlantTarget
from core.application.joint_power_allocator import JointPowerAllocator
from core.domain.capability_snapshot import DeviceCapabilityRecord, DeviceCapabilitySnapshot
from core.domain.control_value import (
    ControlQuantity, ControlScope, ControlValue, ControlValueKind, ControlValueRole,
)
from core.domain.device_capability import DevicePQCapability, DeviceTechnology


def target(quantity: ControlQuantity, amount: float, device: str = "POI") -> ControlValue:
    return ControlValue(
        quantity, ControlScope.POI, device,
        ControlValueRole.ALLOCATION_TARGET, ControlValueKind.ABSOLUTE,
        amount, "MW" if quantity == ControlQuantity.ACTIVE_POWER else "Mvar",
    )


def capability() -> DeviceCapabilitySnapshot:
    device = DevicePQCapability(
        device_id="wtg",
        technology=DeviceTechnology.WIND,
        p_current=0.0, q_current=0.0,
        p_min=-10.0, p_max=10.0, q_min=-10.0, q_max=10.0,
        s_max=10.0,
    )
    return DeviceCapabilitySnapshot(
        records=(
            DeviceCapabilityRecord(
                device, 100, 1,
                quality_good=True,
                p_authorized=True,
                q_authorized=True,
            ),
        ),
        generation=1, sampled_at_ns=100, valid_until_ns=200,
    )


def test_plant_targets_allocate_together() -> None:
    targets = JointPlantTarget(
        active=target(ControlQuantity.ACTIVE_POWER, 6.0),
        reactive=target(ControlQuantity.REACTIVE_POWER, 6.0),
    )
    result = JointPlantAllocator(JointPowerAllocator()).allocate(
        targets, capability(), now_ns=110, maximum_age_ns=30,
    )
    assert result.feasible
    assert result.active[0].value == pytest.approx(6.0)
    assert result.reactive[0].value == pytest.approx(6.0)


def test_plant_targets_with_different_boundaries_rejected() -> None:
    with pytest.raises(ValueError, match="same plant boundary"):
        JointPlantTarget(
            active=target(ControlQuantity.ACTIVE_POWER, 6.0),
            reactive=target(ControlQuantity.REACTIVE_POWER, 6.0, "other"),
        )


def test_individually_possible_but_jointly_impossible_fails_closed() -> None:
    targets = JointPlantTarget(
        active=target(ControlQuantity.ACTIVE_POWER, 8.0),
        reactive=target(ControlQuantity.REACTIVE_POWER, 8.0),
    )
    result = JointPlantAllocator(JointPowerAllocator()).allocate(
        targets, capability(), now_ns=110, maximum_age_ns=30,
    )
    assert not result.feasible
    assert result.active == result.reactive == ()


def test_p_only_control_freezes_q_and_emits_no_reactive_setpoints() -> None:
    target_p = JointPlantTarget(
        active=target(ControlQuantity.ACTIVE_POWER, 8.0),
        reactive=None,
    )
    result = JointPlantAllocator(JointPowerAllocator()).allocate(
        target_p, capability(), now_ns=110, maximum_age_ns=30,
    )
    assert result.feasible
    assert len(result.active) == 1
    assert result.active[0].value == pytest.approx(8.0)
    assert result.reactive == ()


def test_q_only_control_freezes_p_and_emits_no_active_setpoints() -> None:
    target_q = JointPlantTarget(
        active=None,
        reactive=target(ControlQuantity.REACTIVE_POWER, -6.0),
    )
    result = JointPlantAllocator(JointPowerAllocator()).allocate(
        target_q, capability(), now_ns=110, maximum_age_ns=30,
    )
    assert result.feasible
    assert result.active == ()
    assert result.reactive[0].value == pytest.approx(-6.0)


def test_neither_control_function_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires P or Q"):
        JointPlantTarget(active=None, reactive=None)
