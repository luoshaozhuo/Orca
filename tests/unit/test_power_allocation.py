"""功率分配请求与设备能力的领域校验（安全边界：非法输入一律拒绝）。"""
from __future__ import annotations

import pytest

from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.power_allocation import (
    DevicePowerCapability,
    PowerAllocationRequest,
)


def capability(**overrides: object) -> DevicePowerCapability:
    values = {
        "device_id": "wtg",
        "current": 5.0,
        "minimum": 0.0,
        "maximum": 10.0,
    }
    values.update(overrides)
    return DevicePowerCapability(**values)  # type: ignore[arg-type]


def target(**overrides: object) -> ControlValue:
    values = {
        "quantity": ControlQuantity.ACTIVE_POWER,
        "scope": ControlScope.POI,
        "target": "POI",
        "role": ControlValueRole.ALLOCATION_TARGET,
        "kind": ControlValueKind.ABSOLUTE,
        "value": 6.0,
        "unit": "MW",
    }
    values.update(overrides)
    return ControlValue(**values)  # type: ignore[arg-type]


def test_capability_validation() -> None:
    with pytest.raises(ValueError, match="device_id"):
        capability(device_id="")
    with pytest.raises(ValueError, match="finite"):
        capability(current=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        capability(current=True)
    with pytest.raises(ValueError, match="interval or weight"):
        capability(minimum=10.0, maximum=0.0)
    with pytest.raises(ValueError, match="interval or weight"):
        capability(weight=0.0)
    with pytest.raises(ValueError, match="feasible"):
        capability(current=11.0)
    # 不可控设备允许当前值处于区间外（如限功率运行）。
    capability(current=11.0, controllable=False)


def test_request_validation() -> None:
    with pytest.raises(ValueError, match="ALLOCATION_TARGET"):
        PowerAllocationRequest(
            target=target(role=ControlValueRole.CANDIDATE_TARGET),
            devices=(capability(),),
        )
    with pytest.raises(ValueError, match="absolute"):
        PowerAllocationRequest(
            target=target(kind=ControlValueKind.DELTA),
            devices=(capability(),),
        )
    # ControlValue 契约先行拒绝非 P/Q 分配目标。
    with pytest.raises(ValueError, match="P or Q only"):
        PowerAllocationRequest(
            target=target(quantity=ControlQuantity.FREQUENCY, unit="Hz"),
            devices=(capability(),),
        )
    with pytest.raises(ValueError, match="POI or plant scoped"):
        PowerAllocationRequest(
            target=target(scope=ControlScope.DEVICE, target="wtg"),
            devices=(capability(),),
        )
    with pytest.raises(ValueError, match="duplicate"):
        PowerAllocationRequest(
            target=target(),
            devices=(capability(), capability()),
        )
