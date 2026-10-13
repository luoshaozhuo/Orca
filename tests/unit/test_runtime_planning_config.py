"""任务规划配置严格校验和应用自动装配测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.config import OrcaConfig


def configuration(*, planning: dict | None = None) -> dict:
    task = {
        "controller_id": "agc",
        "interval": 1.0,
        "resource_ids": ["wtg"],
    }
    if planning is not None:
        task["planning"] = planning
    return {
        "site": {"site_id": "test"},
        "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
        "resources": {
            "poi": {
                "type": "poi",
                "inputs": {"p": {"point_id": "active_power"}},
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
            "agc": {
                "type": "agc",
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
        "tasks": {"active": task},
    }


def planning() -> dict:
    return {
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
            }
        ],
    }


def test_planning_is_opt_in() -> None:
    config = OrcaConfig.model_validate(configuration())
    assert config.tasks["active"].planning is None


def test_planning_is_validated_and_retained() -> None:
    config = OrcaConfig.model_validate(configuration(planning=planning()))
    assert config.tasks["active"].planning is not None
    assert config.tasks["active"].planning.devices[0].p_auto


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorized_sources", []),
        ("minimum", float("nan")),
        ("maximum", -1.0),
        ("devices", []),
    ],
)
def test_unsafe_planning_config_is_rejected(field: str, value: object) -> None:
    payload = planning()
    payload[field] = value
    with pytest.raises(ValidationError):
        OrcaConfig.model_validate(configuration(planning=payload))


def test_task_cannot_plan_unknown_device() -> None:
    payload = planning()
    payload["devices"][0]["device_id"] = "unregistered"
    with pytest.raises(ValidationError, match="planning device not in task resources"):
        OrcaConfig.model_validate(configuration(planning=payload))


def test_storage_automatic_p_without_soc_mapping_is_rejected() -> None:
    payload = planning()
    payload["devices"][0]["technology"] = "storage"
    with pytest.raises(ValidationError, match="SOC mapping"):
        OrcaConfig.model_validate(configuration(planning=payload))


def test_unregistered_device_measurement_is_rejected() -> None:
    payload = planning()
    payload["devices"][0]["q_point"] = "unregistered"
    with pytest.raises(ValidationError, match="measurement not registered"):
        OrcaConfig.model_validate(configuration(planning=payload))


def test_unregistered_poi_measurement_is_rejected() -> None:
    payload = planning()
    payload["poi_active_point"] = "unregistered"
    with pytest.raises(ValidationError, match="POI measurement not registered"):
        OrcaConfig.model_validate(configuration(planning=payload))
