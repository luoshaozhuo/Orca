"""AGC 限值模式：限值是约束而非定值，可用出力在边界内释放。"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from core.application.control_laws import ActiveLimitLaw
from core.application.control_pipeline_factory import build_control_pipeline
from core.domain.config import ControlPipelineConfig, OrcaConfig
from core.domain.control_law import ControlLawInput
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


def limit_reference(kind: ControlValueKind, amount: float) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
        ControlValueRole.REFERENCE, kind, amount, "MW",
    )


def measurement(amount: float) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
        ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE, amount, "MW",
    )


def sample(
    kind: ControlValueKind,
    limit: float,
    available: float,
    measured: float = 0.0,
) -> ControlLawInput:
    return ControlLawInput(
        reference=limit_reference(kind, limit),
        measurement=measurement(measured),
        dt_s=0.1,
        minimum=0.0,
        maximum=100.0,
        feedforward=available,
    )


class TestActiveLimitLaw:
    def test_available_below_upper_limit_releases_available(self) -> None:
        # 可用 60 MW 低于上限 90 MW：目标是 60 MW，而不是追踪 90 MW。
        result = ActiveLimitLaw().calculate(
            sample(ControlValueKind.UPPER_LIMIT, 90.0, 60.0)
        )
        assert result.candidate.value == pytest.approx(60.0)
        assert result.candidate.kind == ControlValueKind.ABSOLUTE

    def test_available_above_upper_limit_capped(self) -> None:
        result = ActiveLimitLaw().calculate(
            sample(ControlValueKind.UPPER_LIMIT, 90.0, 120.0)
        )
        assert result.candidate.value == pytest.approx(90.0)

    def test_lower_limit_raises_target_floor(self) -> None:
        result = ActiveLimitLaw().calculate(
            sample(ControlValueKind.LOWER_LIMIT, 20.0, 5.0)
        )
        assert result.candidate.value == pytest.approx(20.0)

    def test_lower_limit_releases_available_above_floor(self) -> None:
        result = ActiveLimitLaw().calculate(
            sample(ControlValueKind.LOWER_LIMIT, 20.0, 50.0)
        )
        assert result.candidate.value == pytest.approx(50.0)

    def test_cycle_limits_still_apply(self) -> None:
        bounded = ControlLawInput(
            reference=limit_reference(ControlValueKind.UPPER_LIMIT, 90.0),
            measurement=measurement(0.0),
            dt_s=0.1, minimum=0.0, maximum=40.0, feedforward=60.0,
        )
        assert ActiveLimitLaw().calculate(bounded).candidate.value == pytest.approx(40.0)

    def test_absolute_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="limit reference"):
            ActiveLimitLaw().calculate(
                sample(ControlValueKind.ABSOLUTE, 90.0, 60.0)
            )

    def test_missing_available_estimate_rejected(self) -> None:
        # 非有限可用出力在输入契约层即被拒绝。
        with pytest.raises(ValueError, match="finite"):
            sample(ControlValueKind.UPPER_LIMIT, 90.0, math.nan)

    def test_reset_is_stateless(self) -> None:
        law = ActiveLimitLaw()
        law.calculate(sample(ControlValueKind.UPPER_LIMIT, 90.0, 60.0))
        law.reset()
        assert law.calculate(
            sample(ControlValueKind.UPPER_LIMIT, 90.0, 70.0)
        ).candidate.value == pytest.approx(70.0)


def configuration(**task_overrides: object) -> dict:
    task: dict = {
        "controller_id": "agc",
        "interval": 1.0,
        "resource_ids": ["wtg"],
    }
    planning: dict = {
        "target": "POI",
        "poi_device_id": "poi",
        "poi_active_point": "active_power",
        "minimum": 0.0,
        "maximum": 100.0,
        "authorized_sources": ["GRID"],
        "devices": [
            {
                "device_id": "wtg",
                "technology": "wind",
                "p_point": "active_power",
                "q_point": "reactive_power",
                "p_min": 0.0,
                "p_max": 100.0,
                "q_min": -50.0,
                "q_max": 50.0,
                "s_max": 110.0,
                "p_auto": True,
            }
        ],
    }
    planning.update(task_overrides)
    task["planning"] = planning
    return {
        "site": {"site_id": "test"},
        "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
        "resources": {
            "poi": {
                "type": "poi",
                "inputs": {
                    "p": {"point_id": "active_power"},
                    "avail": {"point_id": "available_power"},
                },
            },
            "dispatch": {
                "type": "external",
                "inputs": {"lim": {"point_id": "active_limit"}},
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
                "strategy": "noop",
                "pipeline": {"law": {"algorithm": "active_limit"}},
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


LIMIT_REFERENCE_INPUT = {
    "purpose": "ACTIVE_LIMIT",
    "source": "GRID",
    "resource_id": "dispatch",
    "point_id": "active_limit",
    "valid_for_s": 5.0,
    "kind": "UPPER_LIMIT",
}


class TestLimitModeConfig:
    def test_valid_limit_mode_accepted(self) -> None:
        config = OrcaConfig.model_validate(configuration(
            mode="LIMIT",
            poi_available_point="available_power",
            reference_inputs=[LIMIT_REFERENCE_INPUT],
        ))
        assert config.tasks["active"].planning is not None
        assert config.tasks["active"].planning.mode.value == "LIMIT"

    def test_limit_mode_requires_available_point(self) -> None:
        with pytest.raises(ValidationError, match="available-power measurement"):
            OrcaConfig.model_validate(configuration(
                mode="LIMIT", reference_inputs=[LIMIT_REFERENCE_INPUT],
            ))

    def test_available_point_rejected_in_setpoint_mode(self) -> None:
        with pytest.raises(ValidationError, match="only used in LIMIT mode"):
            OrcaConfig.model_validate(configuration(
                poi_available_point="available_power",
            ))

    def test_limit_reference_requires_kind(self) -> None:
        broken = {**LIMIT_REFERENCE_INPUT}
        del broken["kind"]
        with pytest.raises(ValidationError, match="explicit limit kind"):
            OrcaConfig.model_validate(configuration(
                mode="LIMIT",
                poi_available_point="available_power",
                reference_inputs=[broken],
            ))

    def test_kind_rejected_for_non_limit_purpose(self) -> None:
        broken = {**LIMIT_REFERENCE_INPUT, "purpose": "ACTIVE_BASE"}
        with pytest.raises(ValidationError, match="only valid for ACTIVE_LIMIT"):
            OrcaConfig.model_validate(configuration(
                reference_inputs=[broken],
            ))

    def test_active_base_rejected_in_limit_mode(self) -> None:
        broken = {**LIMIT_REFERENCE_INPUT, "purpose": "ACTIVE_BASE"}
        del broken["kind"]
        with pytest.raises(ValidationError, match="unsupported planning reference purpose"):
            OrcaConfig.model_validate(configuration(
                mode="LIMIT",
                poi_available_point="available_power",
                reference_inputs=[broken],
            ))

    def test_limit_mode_requires_active_limit_law(self) -> None:
        config_dict = configuration(
            mode="LIMIT",
            poi_available_point="available_power",
            reference_inputs=[LIMIT_REFERENCE_INPUT],
        )
        config_dict["controllers"]["agc"]["pipeline"] = {"law": {"algorithm": "direct"}}
        with pytest.raises(ValidationError, match="requires the active_limit control law"):
            OrcaConfig.model_validate(config_dict)

    def test_active_limit_law_rejected_in_setpoint_mode(self) -> None:
        with pytest.raises(ValidationError, match="active_limit control law requires LIMIT"):
            OrcaConfig.model_validate(configuration())

    def test_unregistered_available_point_rejected(self) -> None:
        config_dict = configuration(
            mode="LIMIT",
            poi_available_point="missing_point",
            reference_inputs=[LIMIT_REFERENCE_INPUT],
        )
        with pytest.raises(ValidationError, match="POI measurement not registered"):
            OrcaConfig.model_validate(config_dict)


def test_factory_builds_active_limit_law() -> None:
    pipeline = build_control_pipeline(
        ControlPipelineConfig.model_validate({"law": {"algorithm": "active_limit"}})
    )
    assert isinstance(pipeline.law, ActiveLimitLaw)
