"""协调事件去重与审计字段：同一事件不重复贡献，结果携带诊断信息。"""
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


def base(amount: float = 50.0) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
        ControlValueRole.REFERENCE, ControlValueKind.ABSOLUTE, amount, "MW",
    )


def demand(
    function: CoordinationFunction,
    amount: float,
    *,
    generation: int = 0,
    event_id: str | None = None,
) -> ControlDemand:
    return ControlDemand(
        function=function,
        value=ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
            ControlValueRole.CONTROL_DEMAND, ControlValueKind.DELTA, amount, "MW",
        ),
        generation=generation,
        event_id=event_id,
    )


def test_same_event_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="same event cannot contribute"):
        CoordinationContext(
            base=base(),
            demands=(
                demand(CoordinationFunction.PFR, 2.0, event_id="evt-1"),
                demand(CoordinationFunction.IR, 1.0, event_id="evt-1"),
            ),
        )


def test_distinct_events_accepted() -> None:
    context = CoordinationContext(
        base=base(),
        demands=(
            demand(CoordinationFunction.PFR, 2.0, event_id="evt-1"),
            demand(CoordinationFunction.IR, 1.0, event_id="evt-2"),
        ),
    )
    result = ControlCoordinator(CoordinationPolicyConfig()).coordinate(context)
    assert result.event_ids == ("evt-1", "evt-2")


def test_empty_event_id_rejected() -> None:
    with pytest.raises(ValueError, match="event_id cannot be empty"):
        demand(CoordinationFunction.PFR, 2.0, event_id="")


def test_result_carries_audit_fields() -> None:
    context = CoordinationContext(
        base=base(50.0),
        demands=(
            demand(CoordinationFunction.PFR, 2.0, generation=7, event_id="evt-9"),
        ),
        base_generation=5,
    )
    result = ControlCoordinator(CoordinationPolicyConfig()).coordinate(context)
    assert result.base == pytest.approx(50.0)
    assert result.generation == 7
    assert result.event_ids == ("evt-9",)
    assert result.target.value == pytest.approx(52.0)


def test_negative_generations_rejected() -> None:
    with pytest.raises(ValueError, match="generation must be nonnegative"):
        demand(CoordinationFunction.PFR, 2.0, generation=-1)
    with pytest.raises(ValueError, match="base generation must be nonnegative"):
        CoordinationContext(base=base(), demands=(), base_generation=-1)
