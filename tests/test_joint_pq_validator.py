"""P/Q 独立目标合并后的联合安全校验。"""
from __future__ import annotations

import pytest

from core.application.joint_pq_validator import JointPQValidator
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.device_capability import DevicePQCapability, DeviceTechnology


def capability(*, p_auto: bool = True) -> DevicePQCapability:
    return DevicePQCapability(
        device_id="device",
        technology=DeviceTechnology.WIND,
        p_current=3.0,
        q_current=4.0,
        p_min=0.0,
        p_max=10.0,
        q_min=-10.0,
        q_max=10.0,
        s_max=10.0,
        p_auto=p_auto,
    )


def setpoint(quantity: ControlQuantity, amount: float) -> ControlValue:
    return ControlValue(
        quantity=quantity,
        scope=ControlScope.DEVICE,
        target="device",
        role=ControlValueRole.DEVICE_SETPOINT,
        kind=ControlValueKind.ABSOLUTE,
        value=amount,
        unit="MW" if quantity == ControlQuantity.ACTIVE_POWER else "Mvar",
    )


def test_jointly_safe_targets_are_accepted() -> None:
    plan = JointPQValidator().validate(
        (capability(),),
        (setpoint(ControlQuantity.ACTIVE_POWER, 6.0),),
        (setpoint(ControlQuantity.REACTIVE_POWER, 6.0),),
    )
    assert len(plan.active) == len(plan.reactive) == 1


def test_independently_valid_but_jointly_invalid_targets_are_rejected() -> None:
    with pytest.raises(ValueError, match="apparent power"):
        JointPQValidator().validate(
            (capability(),),
            (setpoint(ControlQuantity.ACTIVE_POWER, 8.0),),
            (setpoint(ControlQuantity.REACTIVE_POWER, 8.0),),
        )


def test_absent_q_uses_current_reactive_power() -> None:
    with pytest.raises(ValueError, match="apparent power"):
        JointPQValidator().validate(
            (capability(),),
            (setpoint(ControlQuantity.ACTIVE_POWER, 10.0),),
            (),
        )


def test_manual_p_is_never_overwritten() -> None:
    with pytest.raises(ValueError, match="not auto-controlled"):
        JointPQValidator().validate(
            (capability(p_auto=False),),
            (setpoint(ControlQuantity.ACTIVE_POWER, 3.0),),
            (),
        )


def test_duplicate_setpoints_are_rejected() -> None:
    p = setpoint(ControlQuantity.ACTIVE_POWER, 3.0)
    with pytest.raises(ValueError, match="duplicate"):
        JointPQValidator().validate((capability(),), (p, p), ())
