"""应用层规划自动装配、权限及算法切换测试。"""
from __future__ import annotations

import pytest

from control.application import ControlApplication
from core.domain.config import ControlPipelineConfig, OrcaConfig


def config() -> OrcaConfig:
    return OrcaConfig.model_validate(
        {
            "site": {"site_id": "test"},
            "wind_hub": {
                "collector": {"target": "127.0.0.1:50051"},
            },
            "resources": {
                "poi": {
                    "type": "poi",
                    "inputs": {"p": {"point_id": "p"}},
                },
                "wtg": {
                    "type": "wind",
                    "inputs": {
                        "p": {"point_id": "p"},
                        "q": {"point_id": "q"},
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
                            "primary": "GRID",
                        }
                    ],
                },
            },
            "tasks": {
                "active": {
                    "controller_id": "agc",
                    "interval": 1.0,
                    "resource_ids": ["wtg"],
                    "planning": {
                        "target": "POI",
                        "poi_device_id": "poi",
                        "poi_active_point": "p",
                        "minimum": 0.0,
                        "maximum": 10.0,
                        "authorized_sources": ["GRID"],
                        "devices": [
                            {
                                "device_id": "wtg",
                                "technology": "wind",
                                "p_point": "p",
                                "q_point": "q",
                                "p_min": 0.0,
                                "p_max": 10.0,
                                "q_min": -5.0,
                                "q_max": 5.0,
                                "s_max": 10.0,
                                "p_auto": True,
                            }
                        ],
                    },
                },
            },
        }
    )


def test_application_auto_wires_read_only_planning() -> None:
    app = ControlApplication(config())
    assert app.runtime._planning_tasks == frozenset({"active"})
    assert app.runtime._planning_cycle is not None
    assert app.runtime.write_enabled is False
    assert not app._can_plan_device("active", "wtg", "ACTIVE_POWER")


def test_pipeline_replacement_requires_authorization() -> None:
    app = ControlApplication(config())
    with pytest.raises(PermissionError):
        app.replace_task_pipeline(
            "active",
            ControlPipelineConfig(),
            authorized=False,
        )


def test_stopped_pipeline_can_be_replaced_without_restarting() -> None:
    app = ControlApplication(config())
    next_config = ControlPipelineConfig.model_validate(
        {"law": {"algorithm": "p", "kp": 0.2}}
    )
    app.replace_task_pipeline("active", next_config, authorized=True)
    assert app.runtime.states["active"] == "STOPPED"
    assert app.runtime.planning_results == {}


def test_running_pipeline_cannot_be_replaced() -> None:
    app = ControlApplication(config())
    app.runtime.states["active"] = "RUNNING"
    with pytest.raises(RuntimeError, match="stopped task"):
        app.replace_task_pipeline(
            "active", ControlPipelineConfig(), authorized=True,
        )
