"""有功多控制需求协调算法测试。"""
from __future__ import annotations

import pytest

from core.application.control_coordinator import ControlCoordinator
from core.domain.config import CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import (
    ControlDemand,
    CoordinationContext,
    CoordinationFunction,
)


def value(
    amount: float,
    *,
    role: ControlValueRole,
    kind: ControlValueKind,
) -> ControlValue:
    return ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target="POI",
        role=role,
        kind=kind,
        value=amount,
        unit="MW",
    )


def context(**demands: float) -> CoordinationContext:
    items = tuple(
        ControlDemand(
            CoordinationFunction(function),
            value(
                amount,
                role=ControlValueRole.CONTROL_DEMAND,
                kind=ControlValueKind.DELTA,
            ),
        )
        for function, amount in demands.items()
    )
    return CoordinationContext(
        base=value(
            100.0,
            role=ControlValueRole.REFERENCE,
            kind=ControlValueKind.ABSOLUTE,
        ),
        demands=items,
    )


def test_default_adds_same_direction_agc_and_pfr() -> None:
    coordinator = ControlCoordinator(CoordinationPolicyConfig())
    result = coordinator.coordinate(context(agc=5.0, pfr=10.0))
    assert result.dp_coord == pytest.approx(15.0)
    assert result.target.value == pytest.approx(115.0)


def test_default_suppresses_opposite_agc() -> None:
    coordinator = ControlCoordinator(CoordinationPolicyConfig())
    result = coordinator.coordinate(context(agc=-5.0, pfr=10.0))
    assert result.dp_coord == pytest.approx(10.0)
    decisions = {item.function: item for item in result.contributions}
    assert decisions[CoordinationFunction.AGC].accepted == 0.0
    assert "suppressed" in decisions[CoordinationFunction.AGC].reason


def test_default_ir_overrides_agc_and_pfr() -> None:
    coordinator = ControlCoordinator(CoordinationPolicyConfig())
    result = coordinator.coordinate(context(agc=5.0, pfr=10.0, ir=-3.0))
    assert result.dp_coord == pytest.approx(-3.0)


def test_sum_uses_non_normalized_weights() -> None:
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "sum",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "parameters": {
                "weights": {"agc": 0.5, "pfr": 1.0, "ir": 1.0}
            },
        }
    )
    result = ControlCoordinator(policy).coordinate(
        context(agc=5.0, pfr=10.0, ir=-3.0)
    )
    assert result.dp_coord == pytest.approx(9.5)


def test_priority_rejects_equal_highest_priority() -> None:
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "priority",
            "priority": {"ir": 1, "pfr": 1, "agc": 1},
            "parameters": {"tie_break": "reject"},
        }
    )
    with pytest.raises(ValueError, match="equal coordination priority"):
        ControlCoordinator(policy).coordinate(
            context(agc=5.0, pfr=10.0, ir=-3.0)
        )


def test_priority_fixed_order_resolves_equal_priority() -> None:
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "priority",
            "priority": {"ir": 1, "pfr": 1, "agc": 1},
            "parameters": {
                "tie_break": "fixed_order",
                "order": ["pfr", "ir", "agc"],
            },
        }
    )
    result = ControlCoordinator(policy).coordinate(
        context(agc=5.0, pfr=10.0, ir=-3.0)
    )
    assert result.dp_coord == pytest.approx(10.0)


def test_exact_rule_does_not_partially_match_three_functions() -> None:
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "sum",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "rules": [
                {
                    "match": ["agc", "pfr"],
                    "algorithm": "exclusive",
                    "parameters": {
                        "select": "fixed_order",
                        "order": ["pfr", "agc"],
                    },
                }
            ],
        }
    )
    coordinator = ControlCoordinator(policy)

    two = coordinator.coordinate(context(agc=5.0, pfr=10.0))
    assert two.dp_coord == pytest.approx(10.0)
    assert two.matched_rule is not None

    three = coordinator.coordinate(context(agc=5.0, pfr=10.0, ir=-3.0))
    assert three.dp_coord == pytest.approx(12.0)
    assert three.matched_rule is None


def test_directional_suppress_keeps_configured_dominant() -> None:
    policy = CoordinationPolicyConfig.model_validate(
        {
            "algorithm": "directional",
            "priority": {"ir": 1, "pfr": 2, "agc": 3},
            "parameters": {
                "same_direction": "sum",
                "opposite_direction": "suppress",
                "dominant": "pfr",
            },
        }
    )
    result = ControlCoordinator(policy).coordinate(context(agc=-5.0, pfr=10.0))
    assert result.dp_coord == pytest.approx(10.0)


def test_mixed_target_semantics_are_rejected_before_coordination() -> None:
    base = value(
        100.0,
        role=ControlValueRole.REFERENCE,
        kind=ControlValueKind.ABSOLUTE,
    )
    invalid = ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target="OTHER_POI",
        role=ControlValueRole.CONTROL_DEMAND,
        kind=ControlValueKind.DELTA,
        value=1.0,
        unit="MW",
    )
    with pytest.raises(ValueError, match="incompatible"):
        CoordinationContext(
            base=base,
            demands=(ControlDemand(CoordinationFunction.AGC, invalid),),
        )
