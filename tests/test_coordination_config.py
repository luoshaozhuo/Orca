"""协调配置加载、参数模式及精确规则校验测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.domain.config import CoordinationPolicyConfig
from core.domain.coordination import CoordinationAlgorithm
from infrastructure.config.yaml import load_config


CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_coordination_yaml_loads_default_policy() -> None:
    config = load_config(CONFIGS)
    policy = config.coordination.active_power
    assert policy.algorithm == CoordinationAlgorithm.DEFAULT
    assert policy.parameters.weights
    assert not policy.rules


@pytest.mark.parametrize(
    "raw",
    [
        {
            "algorithm": "sum",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "parameters": {"dominant": "ir"},
        },
        {
            "algorithm": "sum",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "parameters": {"weights": {"agc": -1.0}},
        },
        {
            "algorithm": "priority",
            "priority": {"ir": 1, "pfr": 1, "agc": 1},
            "parameters": {
                "tie_break": "fixed_order",
                "order": ["ir", "pfr"],
            },
        },
        {
            "algorithm": "directional",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "parameters": {
                "opposite_direction": "suppress",
            },
        },
    ],
)
def test_invalid_algorithm_parameters_are_rejected(raw: dict) -> None:
    with pytest.raises(ValidationError):
        CoordinationPolicyConfig.model_validate(raw)


def test_duplicate_exact_rule_sets_are_rejected() -> None:
    raw = {
        "algorithm": "sum",
        "priority": {"ir": 1, "pfr": 2, "agc": 3},
        "rules": [
            {
                "match": ["agc", "pfr"],
                "algorithm": "sum",
            },
            {
                "match": ["pfr", "agc"],
                "algorithm": "sum",
            },
        ],
    }
    with pytest.raises(ValidationError, match="duplicate coordination rules"):
        CoordinationPolicyConfig.model_validate(raw)


def test_unknown_parameter_is_rejected() -> None:
    raw = {
        "algorithm": "sum",
        "priority": {"ir": 1, "pfr": 2, "agc": 3},
        "parameters": {"unknown": 1},
    }
    with pytest.raises(ValidationError):
        CoordinationPolicyConfig.model_validate(raw)
