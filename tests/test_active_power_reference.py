"""参考值有效期、授权和多功能快照的控制前置验证。"""
from __future__ import annotations

import pytest

from core.application.active_power_reference import active_power_cycle_from_references
from core.application.reference_manager import ReferenceManager
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import CoordinationFunction
from core.domain.reference import (
    ArbitrationMode,
    ControlReference,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
)


def value(amount: float, role: ControlValueRole, kind: ControlValueKind) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER,
        ControlScope.POI,
        "POI",
        role,
        kind,
        amount,
        "MW",
    )


def manager() -> ReferenceManager:
    return ReferenceManager(
        {
            (purpose, "POI"): ReferenceSelection(
                purpose=purpose,
                target="POI",
                mode=ArbitrationMode.FIXED,
                primary=ReferenceSource.GRID,
            )
            for purpose in (
                ReferencePurpose.ACTIVE_BASE,
                ReferencePurpose.PFR_DELTA,
                ReferencePurpose.IR_DELTA,
            )
        }
    )


def publish(
    references: ReferenceManager,
    purpose: ReferencePurpose,
    amount: float,
    kind: ControlValueKind,
) -> None:
    references.publish(
        ControlReference(
            purpose=purpose,
            source=ReferenceSource.GRID,
            control=value(amount, ControlValueRole.REFERENCE, kind),
            timestamp_ns=10,
            valid_until_ns=100,
            generation=1,
        )
    )


def build(
    references: ReferenceManager,
    now_ns: int = 20,
    functions: frozenset[CoordinationFunction] = frozenset(
        {CoordinationFunction.PFR, CoordinationFunction.IR}
    ),
    sources: frozenset[ReferenceSource] = frozenset({ReferenceSource.GRID}),
):
    return active_power_cycle_from_references(
        references,
        target="POI",
        measurement=value(80.0, ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE),
        now_ns=now_ns,
        authorized_sources=sources,
        dt_s=1.0,
        minimum=0.0,
        maximum=120.0,
        active_functions=functions,
    )


def test_authorized_references_produce_single_cycle_snapshot() -> None:
    references = manager()
    publish(references, ReferencePurpose.ACTIVE_BASE, 90.0, ControlValueKind.ABSOLUTE)
    publish(references, ReferencePurpose.PFR_DELTA, 4.0, ControlValueKind.DELTA)
    publish(references, ReferencePurpose.IR_DELTA, -1.0, ControlValueKind.DELTA)
    result = build(references)
    assert result.base.value == 90.0
    assert {item.function: item.value.value for item in result.demands} == {
        CoordinationFunction.PFR: 4.0,
        CoordinationFunction.IR: -1.0,
    }


def test_missing_required_support_does_not_silently_become_zero() -> None:
    references = manager()
    publish(references, ReferencePurpose.ACTIVE_BASE, 90.0, ControlValueKind.ABSOLUTE)
    with pytest.raises(ValueError, match="(pfr|ir) reference unavailable"):
        build(references)


def test_expired_base_blocks_cycle() -> None:
    references = manager()
    publish(references, ReferencePurpose.ACTIVE_BASE, 90.0, ControlValueKind.ABSOLUTE)
    with pytest.raises(ValueError, match="ACTIVE_BASE: reference unavailable"):
        build(references, now_ns=100, functions=frozenset())


def test_missing_source_authorization_blocks_cycle() -> None:
    references = manager()
    publish(references, ReferencePurpose.ACTIVE_BASE, 90.0, ControlValueKind.ABSOLUTE)
    with pytest.raises(ValueError, match="unauthorized"):
        build(references, functions=frozenset(), sources=frozenset())


def test_inactive_support_is_not_implicitly_reused() -> None:
    references = manager()
    publish(references, ReferencePurpose.ACTIVE_BASE, 90.0, ControlValueKind.ABSOLUTE)
    assert build(references, functions=frozenset()).demands == ()
