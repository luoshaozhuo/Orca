"""内部 PFR/IR 频率响应配置契约校验。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.config import OrcaConfig


def configuration(*, references: list[dict], planning: dict) -> dict:
    return {
        "site": {"site_id": "test"},
        "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
        "resources": {
            "poi": {
                "type": "poi",
                "inputs": {
                    "p": {"point_id": "active_power"},
                    "f": {"point_id": "frequency"},
                },
            },
            "wtg": {
                "type": "wind",
                "inputs": {
                    "p": {"point_id": "active_power"},
                    "q": {"point_id": "reactive_power"},
                },
            },
        },
        "controllers": {"pq": {"type": "agc", "references": references}},
        "tasks": {
            "active": {
                "controller_id": "pq",
                "interval": 1.0,
                "resource_ids": ["wtg"],
                "planning": planning,
            },
        },
    }


def planning(**overrides: object) -> dict:
    payload: dict = {
        "target": "POI",
        "poi_device_id": "poi",
        "poi_active_point": "active_power",
        "minimum": 0.0,
        "maximum": 30.0,
        "authorized_sources": ["GRID", "INTERNAL"],
        "active_functions": ["pfr"],
        "devices": [
            {
                "device_id": "wtg",
                "p_point": "active_power",
                "q_point": "reactive_power",
                "technology": "wind",
                "p_min": 0.0,
                "p_max": 30.0,
                "q_min": -5.0,
                "q_max": 5.0,
                "s_max": 35.0,
                "p_auto": True,
            }
        ],
        "frequency": {
            "frequency_point": "frequency",
            "nominal_frequency_hz": 50.0,
            "valid_for_s": 3.0,
            "pfr": {
                "deadband_hz": 0.05,
                "gain_mw_per_hz": 10.0,
                "max_increase_mw": 6.0,
                "max_decrease_mw": 10.0,
            },
        },
    }
    payload.update(overrides)
    return payload


def references(primary: str = "INTERNAL") -> list[dict]:
    return [
        {"purpose": "ACTIVE_BASE", "target": "POI", "mode": "FIXED", "primary": "GRID"},
        {"purpose": "PFR_DELTA", "target": "POI", "mode": "FIXED", "primary": primary},
    ]


def test_internal_pfr_configuration_is_accepted() -> None:
    config = OrcaConfig.model_validate(
        configuration(references=references(), planning=planning())
    )
    frequency = config.tasks["active"].planning.frequency  # type: ignore[union-attr]
    assert frequency is not None
    assert frequency.pfr is not None
    assert frequency.pfr.deadband_hz == pytest.approx(0.05)


def test_internal_primary_requires_tuning_block() -> None:
    payload = planning()
    payload["frequency"] = None
    with pytest.raises(ValidationError, match="requires frequency tuning"):
        OrcaConfig.model_validate(
            configuration(references=references(), planning=payload)
        )


def test_tuning_requires_internal_primary_policy() -> None:
    with pytest.raises(ValidationError, match="INTERNAL primary"):
        OrcaConfig.model_validate(
            configuration(references=references(primary="FFR"), planning=planning())
        )


def test_internal_function_requires_reference_policy() -> None:
    with pytest.raises(ValidationError, match="reference policy"):
        OrcaConfig.model_validate(
            configuration(references=references()[:1], planning=planning())
        )


def test_internal_function_requires_authorized_source() -> None:
    payload = planning(authorized_sources=["GRID"])
    with pytest.raises(ValidationError, match="INTERNAL in authorized_sources"):
        OrcaConfig.model_validate(
            configuration(references=references(), planning=payload)
        )


def test_frequency_point_must_be_registered_on_poi() -> None:
    payload = planning()
    payload["frequency"]["frequency_point"] = "unknown"
    with pytest.raises(ValidationError, match="frequency measurement not registered"):
        OrcaConfig.model_validate(
            configuration(references=references(), planning=payload)
        )


def test_tuning_block_requires_active_function() -> None:
    payload = planning(active_functions=[])
    payload["frequency"]["pfr"] = None
    payload["frequency"]["ir"] = {
        "rocof_threshold_hz_per_s": 0.1,
        "gain_mw_second_per_hz": 50.0,
        "max_increase_mw": 8.0,
        "max_decrease_mw": 8.0,
        "rocof_window_s": 1.0,
    }
    with pytest.raises(ValidationError, match="IR tuning requires IR"):
        OrcaConfig.model_validate(
            configuration(references=references(), planning=payload)
        )
