"""AVC 无功规划配置校验：模式-用途一致性、测点注册与控制器类型约束。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.config import OrcaConfig


def configuration(
    *,
    reactive_planning: dict | None = None,
    controller_type: str = "pq",
    with_active_planning: bool = True,
) -> dict:
    task = {
        "controller_id": "pq",
        "interval": 1.0,
        "resource_ids": ["wtg"],
    }
    if with_active_planning:
        task["planning"] = {
            "target": "POI",
            "poi_device_id": "poi",
            "poi_active_point": "active_power",
            "minimum": 0.0,
            "maximum": 10.0,
            "authorized_sources": ["GRID"],
            "devices": [
                {
                    "device_id": "wtg",
                    "technology": "wind",
                    "p_point": "active_power",
                    "q_point": "reactive_power",
                    "p_min": 0.0,
                    "p_max": 10.0,
                    "q_min": -5.0,
                    "q_max": 5.0,
                    "s_max": 10.0,
                    "p_auto": True,
                    "q_auto": True,
                }
            ],
        }
    if reactive_planning is not None:
        task["reactive_planning"] = reactive_planning
    return {
        "site": {"site_id": "test"},
        "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
        "resources": {
            "poi": {
                "type": "poi",
                "inputs": {
                    "p": {"point_id": "active_power"},
                    "q": {"point_id": "reactive_power"},
                    "u": {"point_id": "voltage"},
                },
            },
            "dispatch": {
                "type": "external",
                "inputs": {"q_ref": {"point_id": "reactive_reference"}},
            },
            "wtg": {
                "type": "wind",
                "inputs": {
                    "p": {"point_id": "active_power"},
                    "q": {"point_id": "reactive_power"},
                },
            },
        },
        "controllers": {
            "pq": {
                "type": controller_type,
                "references": [
                    {
                        "purpose": "ACTIVE_BASE",
                        "target": "POI",
                        "mode": "FIXED",
                        "primary": "GRID",
                    }
                ],
            }
        },
        "tasks": {"plant": task},
    }


def reactive_planning(**overrides: object) -> dict:
    plan: dict = {
        "target": "POI",
        "mode": "REACTIVE_POWER",
        "poi_device_id": "poi",
        "poi_reactive_point": "reactive_power",
        "minimum": -5.0,
        "maximum": 5.0,
        "authorized_sources": ["GRID"],
        "reference_inputs": [
            {
                "purpose": "REACTIVE_BASE",
                "source": "GRID",
                "resource_id": "dispatch",
                "point_id": "reactive_reference",
                "valid_for_s": 5.0,
            }
        ],
    }
    plan.update(overrides)
    return plan


def test_valid_reactive_planning_accepted() -> None:
    config = OrcaConfig.model_validate(configuration(reactive_planning=reactive_planning()))
    plan = config.tasks["plant"].reactive_planning
    assert plan is not None
    assert plan.mode.value == "REACTIVE_POWER"


def test_missing_mode_purpose_reference_rejected() -> None:
    plan = reactive_planning(reference_inputs=[])
    with pytest.raises(ValidationError, match="missing REACTIVE_BASE reference input"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_voltage_mode_requires_voltage_point() -> None:
    plan = reactive_planning(
        mode="VOLTAGE",
        reference_inputs=[
            {
                "purpose": "VOLTAGE_BASE",
                "source": "GRID",
                "resource_id": "dispatch",
                "point_id": "reactive_reference",
                "valid_for_s": 5.0,
            }
        ],
    )
    with pytest.raises(ValidationError, match="VOLTAGE mode requires"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_voltage_point_rejected_in_reactive_power_mode() -> None:
    plan = reactive_planning(poi_voltage_point="voltage")
    with pytest.raises(ValidationError, match="only used in VOLTAGE mode"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_both_plannings_require_pq_controller() -> None:
    with pytest.raises(ValidationError, match="planning requires pq controller"):
        OrcaConfig.model_validate(
            configuration(reactive_planning=reactive_planning(), controller_type="agc")
        )


def test_active_only_planning_requires_agc_controller() -> None:
    with pytest.raises(ValidationError, match="planning requires agc controller"):
        OrcaConfig.model_validate(configuration())


def test_reactive_planning_requires_active_planning() -> None:
    with pytest.raises(ValidationError, match="requires the active planning device set"):
        OrcaConfig.model_validate(
            configuration(
                reactive_planning=reactive_planning(),
                with_active_planning=False,
            )
        )


def test_inverted_reactive_limits_rejected() -> None:
    plan = reactive_planning(minimum=5.0, maximum=-5.0)
    with pytest.raises(ValidationError, match="inverted"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_unauthorized_reference_source_rejected() -> None:
    plan = reactive_planning()
    plan["reference_inputs"][0]["source"] = "MANUAL"
    with pytest.raises(ValidationError, match="not authorized"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_unregistered_reference_measurement_rejected() -> None:
    plan = reactive_planning()
    plan["reference_inputs"][0]["point_id"] = "missing_point"
    with pytest.raises(ValidationError, match="reactive reference measurement not registered"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))


def test_unregistered_poi_reactive_point_rejected() -> None:
    plan = reactive_planning(poi_reactive_point="missing_point")
    with pytest.raises(ValidationError, match="reactive POI measurement not registered"):
        OrcaConfig.model_validate(configuration(reactive_planning=plan))
