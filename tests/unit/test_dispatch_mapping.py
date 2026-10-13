"""安全执行器配置映射必须与资源输出定义相符。"""
from __future__ import annotations

import pytest

from core.application.dispatch_mapping import validate_dispatch_bindings
from core.domain.config import OrcaConfig
from core.domain.control_value import ControlQuantity
from core.domain.safe_dispatch import OutputBinding


def config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
            "resources": {
                "wtg": {
                    "type": "wind",
                    "outputs": {
                        "active_power_setpoint": {
                            "point_id": "p_cmd",
                            "minimum": 0,
                            "maximum": 10,
                        }
                    },
                }
            },
            "controllers": {"agc": {"type": "agc", "strategy": "noop"}},
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 1.0,
                    "resource_ids": ["wtg"],
                }
            },
        }
    )


def binding(point: str = "p_cmd", minimum: float = 0.0) -> OutputBinding:
    return OutputBinding(
        "wtg", ControlQuantity.ACTIVE_POWER, point, minimum, 10.0
    )


def test_registered_dispatch_binding_is_valid() -> None:
    validate_dispatch_bindings(config(), (binding(),))


def test_missing_physical_output_is_rejected() -> None:
    with pytest.raises(ValueError, match="registered physical output"):
        validate_dispatch_bindings(config(), (binding("unknown"),))


def test_binding_cannot_exceed_configured_limits() -> None:
    with pytest.raises(ValueError, match="exceeds configured"):
        validate_dispatch_bindings(config(), (binding(minimum=-5),))


def test_duplicate_binding_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_dispatch_bindings(config(), (binding(), binding()))
