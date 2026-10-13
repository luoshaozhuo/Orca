"""参考源输入契约与显式主备切换测试。"""
from __future__ import annotations

import pytest

from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.reference import (
    ArbitrationMode,
    ControlReference,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
    select_reference,
)


def make_reference(
    source: ReferenceSource = ReferenceSource.GRID,
    *,
    purpose: ReferencePurpose = ReferencePurpose.ACTIVE_BASE,
    quantity: ControlQuantity = ControlQuantity.ACTIVE_POWER,
    kind: ControlValueKind = ControlValueKind.ABSOLUTE,
    value: float = 50.0,
    unit: str = "MW",
    valid_until_ns: int = 100,
) -> ControlReference:
    control = ControlValue(
        quantity=quantity,
        scope=ControlScope.POI,
        target="POI_1",
        role=ControlValueRole.REFERENCE,
        kind=kind,
        value=value,
        unit=unit,
    )
    return ControlReference(
        purpose=purpose,
        source=source,
        control=control,
        timestamp_ns=10,
        valid_until_ns=valid_until_ns,
        generation=2,
    )


def test_rejects_wrong_reference_semantics() -> None:
    with pytest.raises(ValueError, match="expected kind DELTA"):
        make_reference(purpose=ReferencePurpose.IR_DELTA)
    with pytest.raises(ValueError, match="expected kind ABSOLUTE"):
        make_reference(kind=ControlValueKind.DELTA)
    with pytest.raises(ValueError, match="expected quantity"):
        make_reference(
            purpose=ReferencePurpose.VOLTAGE_BASE,
            quantity=ControlQuantity.ACTIVE_POWER,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_rejects_invalid_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        make_reference(value=value)


def test_fixed_source_rejects_other_sources() -> None:
    policy = ReferenceSelection(
        ReferencePurpose.ACTIVE_BASE,
        "POI_1",
        ArbitrationMode.FIXED,
        ReferenceSource.GRID,
    )
    result = select_reference(
        policy,
        (make_reference(ReferenceSource.MANUAL),),
        now_ns=20,
        authorized_sources=frozenset(ReferenceSource),
    )
    assert result is None


def test_backup_requires_explicit_confirmation_and_authorization() -> None:
    policy = ReferenceSelection(
        ReferencePurpose.ACTIVE_BASE,
        "POI_1",
        ArbitrationMode.PRIMARY_BACKUP,
        ReferenceSource.GRID,
        (ReferenceSource.THIRD_PARTY,),
    )
    backup = make_reference(ReferenceSource.THIRD_PARTY)
    for confirmation, authorization in (
        (None, frozenset({ReferenceSource.THIRD_PARTY})),
        (ReferenceSource.THIRD_PARTY, frozenset()),
    ):
        assert (
            select_reference(
                policy,
                (backup,),
                now_ns=20,
                authorized_sources=authorization,
                confirmed_source=confirmation,
            )
            is None
        )
    assert (
        select_reference(
            policy,
            (backup,),
            now_ns=20,
            authorized_sources=frozenset({ReferenceSource.THIRD_PARTY}),
            confirmed_source=ReferenceSource.THIRD_PARTY,
        )
        == backup
    )


def test_expired_and_future_reference_never_selected() -> None:
    policy = ReferenceSelection(
        ReferencePurpose.ACTIVE_BASE,
        "POI_1",
        ArbitrationMode.FIXED,
        ReferenceSource.GRID,
    )
    for now_ns in (9, 100):
        assert (
            select_reference(
                policy,
                (make_reference(),),
                now_ns=now_ns,
                authorized_sources=frozenset({ReferenceSource.GRID}),
            )
            is None
        )


def test_duplicate_active_samples_fail_closed() -> None:
    policy = ReferenceSelection(
        ReferencePurpose.ACTIVE_BASE,
        "POI_1",
        ArbitrationMode.FIXED,
        ReferenceSource.GRID,
    )
    with pytest.raises(ValueError, match="multiple active"):
        select_reference(
            policy,
            (make_reference(), make_reference()),
            now_ns=20,
            authorized_sources=frozenset({ReferenceSource.GRID}),
        )
