"""控制管线严格配置及装配测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.application.control_laws import DirectControlLaw, PIControlLaw
from core.application.control_pipeline_factory import build_control_pipeline
from core.application.control_signal import LowPassSignal
from core.application.control_trajectory import RampTrajectory
from core.domain.config import ControlPipelineConfig, ControllerConfig


def test_pipeline_configuration_assembles_each_stage() -> None:
    config = ControlPipelineConfig.model_validate(
        {
            "signal": {"algorithm": "low_pass", "tau_s": 1.5},
            "trajectory": {
                "algorithm": "ramp",
                "up_per_s": 2.0,
                "down_per_s": 3.0,
            },
            "law": {"algorithm": "pi", "kp": 0.8, "ki": 0.2, "kaw": 0.1},
        }
    )
    pipeline = build_control_pipeline(config)
    assert isinstance(pipeline.signal, LowPassSignal)
    assert isinstance(pipeline.trajectory, RampTrajectory)
    assert isinstance(pipeline.law, PIControlLaw)
    assert pipeline.law.kp == pytest.approx(0.8)


def test_controller_without_pipeline_retains_safe_direct_default() -> None:
    config = ControllerConfig.model_validate(
        {"type": "agc", "strategy": "noop"}
    )
    pipeline = build_control_pipeline(config.pipeline)
    assert isinstance(pipeline.law, DirectControlLaw)


@pytest.mark.parametrize(
    "data",
    [
        {"signal": {"algorithm": "low_pass"}},
        {"signal": {"algorithm": "passthrough", "tau_s": 1.0}},
        {"signal": {"algorithm": "unknown"}},
        {"trajectory": {"algorithm": "ramp", "up_per_s": 1.0}},
        {"trajectory": {"algorithm": "ramp", "up_per_s": -1.0, "down_per_s": 1.0}},
        {"law": {"algorithm": "pi", "kp": 1.0}},
        {"law": {"algorithm": "p", "kp": 1.0, "ki": 1.0}},
        {"law": {"algorithm": "direct", "kp": 0.1}},
        {"law": {"algorithm": "pi", "kp": 1.0, "ki": float("nan")}},
        {"law": {"algorithm": "unsupported"}},
        {"extra": 1},
    ],
)
def test_unsupported_or_incomplete_pipeline_is_rejected(data: dict) -> None:
    with pytest.raises(ValidationError):
        ControlPipelineConfig.model_validate(data)
