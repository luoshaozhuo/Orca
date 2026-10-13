"""参考来源 YAML、模型与装配契约。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.application.reference_manager import ReferenceManager
from core.domain.config import ControllerConfig
from core.domain.reference import ReferencePurpose, ReferenceSource
from infrastructure.config.yaml import load_config

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_reference_policy_loads_from_controller_yaml() -> None:
    config = load_config(CONFIGS)
    references = ReferenceManager.from_controllers(config.controllers)
    policy = references.policies[(ReferencePurpose.ACTIVE_BASE, "POI")]
    assert policy.primary == ReferenceSource.GRID
    assert not references._samples


@pytest.mark.parametrize(
    "policy",
    [
        {"purpose": "PFR_DELTA", "target": "POI", "primary": "INVALID"},
        {"purpose": "PFR_DELTA", "target": "POI", "primary": "INTERNAL",
         "mode": "FIXED", "backups": ["FFR"]},
        {"purpose": "IR_DELTA", "target": "POI", "primary": "INTERNAL",
         "mode": "PRIMARY_BACKUP", "backups": ["INTERNAL"]},
    ],
)
def test_invalid_reference_policy_is_rejected(policy: dict) -> None:
    with pytest.raises(ValidationError):
        ControllerConfig(type="agc", strategy="noop", references=[policy])


def test_duplicate_reference_policy_in_single_controller_rejected() -> None:
    policy = {"purpose": "ACTIVE_BASE", "target": "POI", "primary": "GRID"}
    with pytest.raises(ValidationError, match="duplicate"):
        ControllerConfig(type="agc", strategy="noop", references=[policy, policy])


def test_cross_controller_reference_ownership_rejected() -> None:
    policy = {"purpose": "ACTIVE_BASE", "target": "POI", "primary": "GRID"}
    controllers = {
        "a": ControllerConfig(type="agc", strategy="noop", references=[policy]),
        "b": ControllerConfig(type="agc", strategy="noop", references=[policy]),
    }
    with pytest.raises(ValueError, match="already owned"):
        ReferenceManager.from_controllers(controllers)
