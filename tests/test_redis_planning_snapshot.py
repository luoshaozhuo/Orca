"""Redis 实时测量及参考仲裁规划快照的集成测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.application.reference_manager import ReferenceManager
from core.domain.control_value import (
    ControlQuantity, ControlScope, ControlValue, ControlValueKind, ControlValueRole,
)
from core.domain.reference import (
    ArbitrationMode, ControlReference, ReferencePurpose, ReferenceSelection,
    ReferenceSource,
)
from core.domain.device_capability import DeviceTechnology
from infrastructure.redis.planning_snapshot import (
    ActivePlanningBinding, DevicePlanningBinding, RedisPlanningSnapshotSource,
)


class MockMeasurements:
    def __init__(self, records: dict, *, fail: bool = False) -> None:
        self.records = records
        self.fail = fail
        self.calls = 0

    async def latest(self, pairs: list[tuple[str, str]]) -> dict:
        self.calls += 1
        if self.fail:
            raise ConnectionError("redis unavailable")
        return {point: self.records[point] for point in pairs if point in self.records}


def values(*, bad: bool = False, expired: bool = False) -> dict:
    time = datetime.now(UTC) - timedelta(seconds=10 if expired else 0.1)
    timestamp = time.isoformat()
    return {
        ("poi", "p"): {"value": 5.0, "quality": "good", "timestamp": timestamp},
        ("wtg", "p"): {"value": 5.0, "quality": "bad" if bad else "good", "timestamp": timestamp},
        ("wtg", "q"): {"value": 0.0, "quality": "good", "timestamp": timestamp},
    }


def references() -> ReferenceManager:
    policy = ReferenceSelection(
        ReferencePurpose.ACTIVE_BASE, "POI",
        ArbitrationMode.FIXED, ReferenceSource.GRID,
    )
    result = ReferenceManager({(ReferencePurpose.ACTIVE_BASE, "POI"): policy})
    from time import time_ns

    now = time_ns()
    result.publish(
        ControlReference(
            purpose=ReferencePurpose.ACTIVE_BASE,
            source=ReferenceSource.GRID,
            control=ControlValue(
                ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
                ControlValueRole.REFERENCE, ControlValueKind.ABSOLUTE,
                6.0, "MW",
            ),
            timestamp_ns=now - 1_000_000_000,
            valid_until_ns=now + 10_000_000_000,
            generation=0,
        )
    )
    return result


def bindings() -> dict[str, ActivePlanningBinding]:
    return {
        "active": ActivePlanningBinding(
            target="POI", poi_device_id="poi", poi_active_point="p",
            minimum=0.0, maximum=10.0, dt_s=0.1,
            authorized_sources=frozenset({ReferenceSource.GRID}),
            devices=(
                DevicePlanningBinding(
                    device_id="wtg", p_point="p", q_point="q",
                    technology=DeviceTechnology.WIND,
                    p_min=0, p_max=10, q_min=-5, q_max=5,
                    s_max=10, p_auto=True, q_auto=False,
                ),
            ),
        ),
    }


def source(records: dict, *, allow: bool) -> RedisPlanningSnapshotSource:
    return RedisPlanningSnapshotSource(
        MockMeasurements(records), references(), bindings(),
        generation=7, maximum_age_ns=2_000_000_000,
        can_plan=lambda task, device, quantity: allow,
    )


@pytest.mark.asyncio
async def test_live_snapshot_contains_authorized_active_channel() -> None:
    snapshot = await source(values(), allow=True).acquire("active")
    assert snapshot.active.base.value == 6.0
    assert snapshot.active.measurement.value == 5.0
    assert snapshot.devices.generation == 7
    assert snapshot.devices.bounded()[0].p_auto
    assert not snapshot.devices.bounded()[0].q_auto


@pytest.mark.asyncio
async def test_unauthorized_device_is_not_upgraded_to_auto() -> None:
    snapshot = await source(values(), allow=False).acquire("active")
    assert not snapshot.devices.bounded()[0].p_auto


@pytest.mark.asyncio
async def test_bad_quality_blocks_snapshot() -> None:
    with pytest.raises(ValueError, match="bad measurement quality"):
        await source(values(bad=True), allow=True).acquire("active")


@pytest.mark.asyncio
async def test_expired_measurement_blocks_snapshot() -> None:
    with pytest.raises(ValueError, match="stale"):
        await source(values(expired=True), allow=True).acquire("active")


@pytest.mark.asyncio
async def test_missing_required_device_measurement_blocks_snapshot() -> None:
    records = values()
    records.pop(("wtg", "q"))
    with pytest.raises(ValueError, match="missing measurement"):
        await source(records, allow=True).acquire("active")


@pytest.mark.asyncio
async def test_redis_failure_is_not_suppressed() -> None:
    provider = RedisPlanningSnapshotSource(
        MockMeasurements(values(), fail=True),
        references(), bindings(), generation=7, maximum_age_ns=2_000_000_000,
    )
    with pytest.raises(ConnectionError, match="redis unavailable"):
        await provider.acquire("active")
