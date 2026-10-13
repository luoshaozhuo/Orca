"""场站控制与设备能力分配贯通测试；不调用 Commander。"""
from __future__ import annotations

import pytest

from core.application.active_power_controller import ActivePowerController, ActivePowerCycle
from core.application.active_power_planner import ActivePowerPlanner
from core.application.control_coordinator import ControlCoordinator
from core.application.control_laws import DirectControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_trajectory import PassthroughTrajectory
from core.application.power_allocator import PowerAllocator
from core.domain.config import CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import ControlDemand, CoordinationFunction
from core.domain.power_allocation import DevicePowerCapability


def value(
    amount: float,
    role: ControlValueRole,
    kind: ControlValueKind = ControlValueKind.ABSOLUTE,
) -> ControlValue:
    return ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target="POI",
        role=role,
        kind=kind,
        value=amount,
        unit="MW",
    )


def planner() -> ActivePowerPlanner:
    controller = ActivePowerController(
        pipeline=ControlPipeline(
            trajectory=PassthroughTrajectory(),
            law=DirectControlLaw(),
        ),
        coordinator=ControlCoordinator(
            CoordinationPolicyConfig.model_validate(
                {"algorithm": "sum", "parameters": {"weights": {"pfr": 1.0}}}
            )
        ),
    )
    return ActivePowerPlanner(controller, PowerAllocator())


def cycle() -> ActivePowerCycle:
    return ActivePowerCycle(
        base=value(60.0, ControlValueRole.REFERENCE),
        measurement=value(55.0, ControlValueRole.MEASUREMENT),
        dt_s=1.0,
        minimum=0.0,
        maximum=100.0,
        demands=(
            ControlDemand(
                function=CoordinationFunction.PFR,
                value=value(
                    10.0,
                    ControlValueRole.CONTROL_DEMAND,
                    ControlValueKind.DELTA,
                ),
            ),
        ),
    )


def test_plant_controller_to_device_setpoints() -> None:
    result = planner().plan(
        cycle(),
        (
            DevicePowerCapability("wtg", current=20.0, minimum=0.0, maximum=40.0),
            DevicePowerCapability("pv", current=20.0, minimum=0.0, maximum=40.0),
        ),
    )
    assert result.control.coordination.target.value == pytest.approx(70.0)
    assert result.allocation.feasible
    assert [item.value for item in result.allocation.setpoints] == pytest.approx(
        [35.0, 35.0]
    )


def test_infeasible_allocation_blocks_setpoints() -> None:
    with pytest.raises(ValueError, match="allocation infeasible"):
        planner().plan(
            cycle(),
            (
                DevicePowerCapability("wtg", 10.0, 0.0, 20.0),
                DevicePowerCapability("pv", 10.0, 0.0, 20.0),
            ),
        )
