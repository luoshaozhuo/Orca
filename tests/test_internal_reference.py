"""内部参考生成仅允许已投入、未闭锁的控制功能。"""
from __future__ import annotations

import pytest

from core.application.internal_reference import InternalReferenceService
from core.application.reference_generators import InertiaParameters, PFRParameters
from core.application.reference_manager import ReferenceManager
from core.domain.control_authority import ControlAuthority, ControlFunction
from core.domain.reference import (
    ArbitrationMode,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
)


def service() -> InternalReferenceService:
    policy = ReferenceSelection(
        ReferencePurpose.PFR_DELTA,
        "POI",
        ArbitrationMode.FIXED,
        ReferenceSource.INTERNAL,
    )
    return InternalReferenceService(
        ReferenceManager({(policy.purpose, policy.target): policy}),
        ControlAuthority(),
    )


def test_internal_pfr_requires_function_service_and_unblocked() -> None:
    item = service()
    parameters = PFRParameters(50.0, 0.05, 100.0, 6.0, 10.0)
    arguments = {
        "target": "POI",
        "timestamp_ns": 10,
        "valid_until_ns": 20,
        "generation": 1,
    }
    with pytest.raises(RuntimeError, match="not permitted"):
        item.pfr(49.9, parameters, **arguments)
    assert not item.manager._samples

    item.authority.put_in_service(ControlFunction.PFR, authorized=True)
    reference = item.pfr(49.9, parameters, **arguments)
    assert reference.control.value == pytest.approx(5.0)
    assert (
        item.manager.current(
            ReferencePurpose.PFR_DELTA,
            "POI",
            now_ns=15,
            authorized_sources=frozenset({ReferenceSource.INTERNAL}),
        )
        == reference
    )

    item.authority.block(ControlFunction.PFR, "BAD_FREQUENCY")
    with pytest.raises(RuntimeError, match="not permitted"):
        item.pfr(
            49.8,
            parameters,
            **{
                **arguments,
                "timestamp_ns": 21,
                "valid_until_ns": 31,
            },
        )
    item.manager.clear(ReferencePurpose.PFR_DELTA, "POI")
    assert not item.manager._samples


def test_unconfigured_internal_inertia_rejected() -> None:
    item = service()
    item.authority.put_in_service(ControlFunction.IR, authorized=True)
    with pytest.raises(ValueError, match="not configured"):
        item.inertia(
            -0.1,
            InertiaParameters(20.0, 5.0, 5.0),
            target="POI",
            timestamp_ns=10,
            valid_until_ns=20,
            generation=1,
        )
