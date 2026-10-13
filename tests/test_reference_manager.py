"""参考注册、显式选择及失效封锁测试。"""
from __future__ import annotations

import pytest

from core.application.reference_manager import ReferenceManager
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
)


PURPOSE = ReferencePurpose.PFR_DELTA
TARGET = "POI"


def sample(
    source: ReferenceSource,
    *,
    ts: int = 10,
    generation: int = 1,
    sequence_id: int = 0,
) -> ControlReference:
    control = ControlValue(
        quantity=ControlQuantity.ACTIVE_POWER,
        scope=ControlScope.POI,
        target=TARGET,
        role=ControlValueRole.REFERENCE,
        kind=ControlValueKind.DELTA,
        value=2.0,
        unit="MW",
    )
    return ControlReference(
        purpose=PURPOSE,
        source=source,
        control=control,
        timestamp_ns=ts,
        valid_until_ns=ts + 100,
        generation=generation,
        sequence_id=sequence_id,
    )


def manager() -> ReferenceManager:
    policy = ReferenceSelection(
        PURPOSE,
        TARGET,
        ArbitrationMode.PRIMARY_BACKUP,
        ReferenceSource.INTERNAL,
        (ReferenceSource.FFR,),
    )
    return ReferenceManager({(PURPOSE, TARGET): policy})


def test_internal_and_external_reference_share_semantics() -> None:
    refs = manager()
    refs.publish(sample(ReferenceSource.INTERNAL))
    refs.publish(sample(ReferenceSource.FFR))
    current = refs.current(
        PURPOSE,
        TARGET,
        now_ns=20,
        authorized_sources=frozenset(
            {ReferenceSource.INTERNAL, ReferenceSource.FFR}
        ),
    )
    assert current is not None
    assert current.source == ReferenceSource.INTERNAL


def test_source_switch_is_latched_until_explicit_switch_back() -> None:
    refs = manager()
    refs.publish(sample(ReferenceSource.INTERNAL))
    refs.publish(sample(ReferenceSource.FFR))
    with pytest.raises(PermissionError):
        refs.select_source(
            PURPOSE,
            TARGET,
            ReferenceSource.FFR,
            authorized=False,
        )
    refs.select_source(
        PURPOSE,
        TARGET,
        ReferenceSource.FFR,
        authorized=True,
    )
    allowed = frozenset({ReferenceSource.INTERNAL, ReferenceSource.FFR})
    current = refs.current(PURPOSE, TARGET, now_ns=20, authorized_sources=allowed)
    assert current is not None
    assert current.source == ReferenceSource.FFR
    assert refs.current(
        PURPOSE,
        TARGET,
        now_ns=200,
        authorized_sources=allowed,
    ) is None

    refs.publish(sample(ReferenceSource.INTERNAL, ts=210))
    assert refs.current(
        PURPOSE,
        TARGET,
        now_ns=220,
        authorized_sources=allowed,
    ) is None
    refs.select_source(
        PURPOSE,
        TARGET,
        ReferenceSource.INTERNAL,
        authorized=True,
    )
    current = refs.current(
        PURPOSE,
        TARGET,
        now_ns=220,
        authorized_sources=allowed,
    )
    assert current is not None
    assert current.source == ReferenceSource.INTERNAL


def test_generation_sequence_and_timestamp_are_monotonic() -> None:
    refs = manager()
    refs.publish(sample(ReferenceSource.INTERNAL))
    with pytest.raises(ValueError, match="out-of-order"):
        refs.publish(sample(ReferenceSource.INTERNAL))
    with pytest.raises(ValueError, match="out-of-order"):
        refs.publish(
            sample(
                ReferenceSource.INTERNAL,
                generation=0,
                ts=50,
            )
        )
    refs.publish(
        sample(
            ReferenceSource.INTERNAL,
            generation=1,
            sequence_id=1,
            ts=5,
        )
    )
    refs.publish(sample(ReferenceSource.INTERNAL, generation=2, ts=1))


def test_clear_requires_new_reference() -> None:
    refs = manager()
    refs.publish(sample(ReferenceSource.INTERNAL))
    refs.clear(PURPOSE, TARGET)
    assert (
        refs.current(
            PURPOSE,
            TARGET,
            now_ns=20,
            authorized_sources=frozenset({ReferenceSource.INTERNAL}),
        )
        is None
    )
