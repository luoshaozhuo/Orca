"""设备级有功/无功分配约束和故障闭锁测试。"""
from __future__ import annotations

import pytest

from core.application.power_allocator import PowerAllocator
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.power_allocation import DevicePowerCapability, PowerAllocationRequest


def request(
    target: float,
    *devices: DevicePowerCapability,
    quantity: ControlQuantity = ControlQuantity.ACTIVE_POWER,
) -> PowerAllocationRequest:
    return PowerAllocationRequest(
        target=ControlValue(
            quantity=quantity,
            scope=ControlScope.POI,
            target="POI",
            role=ControlValueRole.ALLOCATION_TARGET,
            kind=ControlValueKind.ABSOLUTE,
            value=target,
            unit="MW" if quantity == ControlQuantity.ACTIVE_POWER else "Mvar",
        ),
        devices=devices,
    )


def device(
    name: str,
    current: float,
    low: float,
    high: float,
    *,
    controllable: bool = True,
    weight: float = 1.0,
) -> DevicePowerCapability:
    return DevicePowerCapability(
        device_id=name,
        current=current,
        minimum=low,
        maximum=high,
        controllable=controllable,
        weight=weight,
    )


def test_weighted_headroom_allocation() -> None:
    result = PowerAllocator().allocate(
        request(
            60.0,
            device("wtg", 10.0, 0.0, 60.0, weight=2.0),
            device("pv", 10.0, 0.0, 60.0, weight=1.0),
        )
    )
    values = {item.target: item.value for item in result.setpoints}
    assert result.feasible
    assert values == pytest.approx({"wtg": 10 + 80 / 3, "pv": 10 + 40 / 3})
    assert result.residual == pytest.approx(0.0)


def test_bypass_power_retained_but_no_setpoint_issued() -> None:
    result = PowerAllocator().allocate(
        request(
            70.0,
            device("manual", 30.0, 0.0, 10.0, controllable=False),
            device("auto", 20.0, 10.0, 60.0),
        )
    )
    assert result.feasible
    assert result.fixed_power == 30.0
    assert [value.target for value in result.setpoints] == ["auto"]
    assert result.setpoints[0].value == pytest.approx(40.0)


def test_saturated_device_redistributes_residual() -> None:
    result = PowerAllocator().allocate(
        request(
            90.0,
            device("small", 10.0, 0.0, 20.0, weight=10.0),
            device("large", 10.0, 0.0, 100.0),
        )
    )
    assert result.feasible
    assert {item.target: item.value for item in result.setpoints} == pytest.approx(
        {"small": 20.0, "large": 70.0}
    )


def test_unreachable_target_yields_no_device_setpoints() -> None:
    result = PowerAllocator().allocate(
        request(120.0, device("wtg", 10.0, 0.0, 50.0))
    )
    assert not result.feasible
    assert not result.setpoints
    assert result.residual == pytest.approx(110.0)


def test_reactive_allocation_preserves_unit() -> None:
    result = PowerAllocator().allocate(
        request(
            -5.0,
            device("svg", 0.0, -10.0, 10.0),
            quantity=ControlQuantity.REACTIVE_POWER,
        )
    )
    assert result.feasible
    assert result.setpoints[0].unit == "Mvar"
    assert result.setpoints[0].value == pytest.approx(-5.0)


def test_duplicate_device_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        request(10.0, device("same", 0, 0, 10), device("same", 0, 0, 10))
