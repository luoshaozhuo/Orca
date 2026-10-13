"""PFR/IR 事件状态机与内部参考接入测试（设计 §7.5、§10.2）。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.frequency_response import (
    FrequencyResponseService,
    IrEventMachine,
    PfrEventMachine,
    ResponsePhase,
    RocofEstimator,
)
from core.application.internal_reference import InternalReferenceService
from core.application.reference_generators import InertiaParameters, PFRParameters
from core.application.reference_manager import ReferenceManager
from core.domain.control_authority import ControlAuthority, ControlFunction
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

pytestmark = pytest.mark.asyncio

GRID = ReferenceSource.GRID
INTERNAL = ReferenceSource.INTERNAL
SECOND = 1_000_000_000


def iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, UTC).isoformat()


class FakeSource:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, object]] = {}

    def set(self, device: str, point: str, value: float, ts_ns: int,
            quality: str = "good") -> None:
        self.records[(device, point)] = {
            "value": value, "quality": quality, "timestamp": iso(ts_ns),
        }

    async def latest(self, pairs):
        return {pair: self.records[pair] for pair in pairs if pair in self.records}


def make_service(
    *,
    pfr: bool = True,
    ir: bool = False,
    recovery_s: float = 0.0,
    threshold: float = 0.1,
    sources: tuple[ReferenceSource, ...] = (GRID, INTERNAL),
    pfr_primary: ReferenceSource = INTERNAL,
) -> tuple[FrequencyResponseService, FakeSource, ReferenceManager, ControlAuthority]:
    policies = {
        (ReferencePurpose.ACTIVE_BASE, "POI"): ReferenceSelection(
            ReferencePurpose.ACTIVE_BASE, "POI", ArbitrationMode.FIXED, GRID,
        ),
        (ReferencePurpose.PFR_DELTA, "POI"): ReferenceSelection(
            ReferencePurpose.PFR_DELTA, "POI", ArbitrationMode.PRIMARY_BACKUP,
            pfr_primary, (INTERNAL,) if pfr_primary != INTERNAL else (),
        ),
        (ReferencePurpose.IR_DELTA, "POI"): ReferenceSelection(
            ReferencePurpose.IR_DELTA, "POI", ArbitrationMode.FIXED, INTERNAL,
        ),
    }
    manager = ReferenceManager(policies)
    authority = ControlAuthority()
    authority.put_in_service(ControlFunction.PFR, authorized=True)
    authority.put_in_service(ControlFunction.IR, authorized=True)
    source = FakeSource()
    service = FrequencyResponseService(
        internal=InternalReferenceService(manager, authority),
        source=source,
        device_id="poi",
        point_id="FREQ",
        target="POI",
        valid_for_ns=10 * SECOND,
        generation=1,
        maximum_age_ns=5 * SECOND,
        base_purpose=ReferencePurpose.ACTIVE_BASE,
        authorized_sources=frozenset(sources),
        pfr=(
            PfrEventMachine(
                PFRParameters(50.0, 0.05, 100.0, 6.0, 10.0), recovery_s=recovery_s,
            )
            if pfr else None
        ),
        ir=(
            IrEventMachine(
                InertiaParameters(50.0, 8.0, 8.0),
                threshold_hz_per_s=threshold,
                recovery_s=recovery_s,
            )
            if ir else None
        ),
        rocof=RocofEstimator(window_s=1.0, min_samples=3) if ir else None,
    )
    return service, source, manager, authority


def publish_agc_base(manager: ReferenceManager, value: float, ts_ns: int) -> None:
    manager.publish(ControlReference(
        purpose=ReferencePurpose.ACTIVE_BASE,
        source=GRID,
        control=ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
            ControlValueRole.REFERENCE, ControlValueKind.ABSOLUTE, value, "MW",
        ),
        timestamp_ns=ts_ns,
        valid_until_ns=ts_ns + 60 * SECOND,
        generation=1,
        sequence_id=ts_ns,
    ))


def pfr_reference(manager: ReferenceManager, now_ns: int) -> ControlReference | None:
    return manager.current(
        ReferencePurpose.PFR_DELTA, "POI",
        now_ns=now_ns, authorized_sources=frozenset({GRID, INTERNAL}),
    )


async def test_pfr_enters_event_outside_deadband() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    publish_agc_base(manager, 80.0, t0)
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None
    assert service.pfr.phase == ResponsePhase.RESPONDING
    assert service.pfr.event is not None
    reference = pfr_reference(manager, t0)
    assert reference is not None
    assert reference.control.value == pytest.approx(5.0)
    assert reference.event_id == service.pfr.event.event_id
    # 高频：-95 MW 受下调限幅 10 MW 截断（非对称限幅）。
    source.set("poi", "FREQ", 51.00, t0 + SECOND)
    await service.update(t0 + SECOND)
    reference = pfr_reference(manager, t0 + SECOND)
    assert reference is not None
    assert reference.control.value == pytest.approx(-10.0)


async def test_pfr_exits_when_frequency_returns_to_deadband() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.phase == ResponsePhase.RESPONDING
    source.set("poi", "FREQ", 50.01, t0 + SECOND)
    await service.update(t0 + SECOND)
    assert service.pfr.phase == ResponsePhase.RECOVERING
    assert service.pfr.event is None
    reference = pfr_reference(manager, t0 + SECOND)
    assert reference is not None
    assert reference.control.value == 0.0
    assert reference.event_id is None
    # 恢复时间为零：下一周期回到 IDLE。
    source.set("poi", "FREQ", 50.00, t0 + 2 * SECOND)
    await service.update(t0 + 2 * SECOND)
    assert service.pfr.phase == ResponsePhase.IDLE


async def test_disturbance_over_many_cycles_keeps_single_event() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    publish_agc_base(manager, 80.0, t0)
    event_ids: set[str] = set()
    for cycle in range(10):
        ts = t0 + cycle * SECOND
        source.set("poi", "FREQ", 49.90 + 0.001 * cycle, ts)
        await service.update(ts)
        assert service.pfr is not None and service.pfr.event is not None
        event_ids.add(service.pfr.event.event_id)
    assert len(event_ids) == 1
    assert service.pfr.event.frozen_base_mw == 80.0


async def test_same_event_repeated_input_does_not_duplicate() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.event is not None
    event_id = service.pfr.event.event_id
    # 同一时间戳重复输入：不重复发布、不重建事件。
    await service.update(t0)
    assert service.pfr.event.event_id == event_id
    sample = manager._samples[(ReferencePurpose.PFR_DELTA, "POI", INTERNAL)]
    assert sample.timestamp_ns == t0
    assert sample.event_id == event_id


async def test_agc_target_change_during_event_keeps_frozen_entry_base() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    publish_agc_base(manager, 80.0, t0)
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.event is not None
    assert service.pfr.event.frozen_base_mw == 80.0
    # 事件期间 AGC 调度目标变化：不重冻结，事件基线保持进入时刻值。
    publish_agc_base(manager, 95.0, t0 + SECOND)
    source.set("poi", "FREQ", 49.85, t0 + SECOND)
    await service.update(t0 + SECOND)
    assert service.pfr.event.frozen_base_mw == 80.0
    assert service.pfr.event.frozen_base_timestamp_ns == t0
    reference = pfr_reference(manager, t0 + SECOND)
    assert reference is not None
    # 0.15 Hz 偏差 → 10 MW，受上调限幅 6 MW 截断。
    assert reference.control.value == pytest.approx(6.0)


async def test_pfr_and_ir_trigger_simultaneously_with_distinct_events() -> None:
    service, source, manager, _ = make_service(ir=True)
    t0 = 100 * SECOND
    # 频率持续快速下降：PFR 死区外且 RoCoF 越限。
    for cycle in range(4):
        ts = t0 + cycle * 250_000_000
        source.set("poi", "FREQ", 50.05 - 0.05 * (cycle + 1), ts)
        await service.update(ts)
    assert service.pfr is not None and service.pfr.phase == ResponsePhase.RESPONDING
    assert service.ir is not None and service.ir.phase == ResponsePhase.RESPONDING
    assert service.pfr.event is not None and service.ir.event is not None
    assert service.pfr.event.event_id != service.ir.event.event_id
    pfr_ref = pfr_reference(manager, t0 + 3 * 250_000_000)
    ir_ref = manager.current(
        ReferencePurpose.IR_DELTA, "POI",
        now_ns=t0 + 3 * 250_000_000, authorized_sources=frozenset({INTERNAL}),
    )
    assert pfr_ref is not None and ir_ref is not None
    assert pfr_ref.control.value > 0
    assert ir_ref.control.value > 0


async def test_internal_external_source_switch_selects_single_contribution() -> None:
    service, source, manager, _ = make_service(
        pfr_primary=ReferenceSource.FFR,
    )
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    # 外部 FFR 参考同事件到达：仲裁只能选中一个来源。
    manager.publish(ControlReference(
        purpose=ReferencePurpose.PFR_DELTA,
        source=ReferenceSource.FFR,
        control=ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
            ControlValueRole.REFERENCE, ControlValueKind.DELTA, 3.0, "MW",
        ),
        timestamp_ns=t0,
        valid_until_ns=t0 + 10 * SECOND,
        generation=1,
        sequence_id=t0,
        event_id="external-event-1",
    ))
    authorized = frozenset({ReferenceSource.FFR, INTERNAL})
    selected = manager.current(
        ReferencePurpose.PFR_DELTA, "POI", now_ns=t0, authorized_sources=authorized,
    )
    assert selected is not None
    assert selected.source == ReferenceSource.FFR
    # 授权切换到内部来源后选择内部样本，不会两个来源同时生效。
    manager.select_source(
        ReferencePurpose.PFR_DELTA, "POI", INTERNAL, authorized=True,
    )
    selected = manager.current(
        ReferencePurpose.PFR_DELTA, "POI", now_ns=t0, authorized_sources=authorized,
    )
    assert selected is not None
    assert selected.source == INTERNAL
    assert selected.control.value == pytest.approx(5.0)


async def test_stale_frequency_input_resets_state_and_blocks_publish() -> None:
    service, source, manager, _ = make_service()
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.phase == ResponsePhase.RESPONDING
    # 测点停滞：10 秒后数据过期，状态机复位且不沿用旧事件。
    later = t0 + 10 * SECOND
    with pytest.raises(ValueError, match="stale"):
        await service.update(later)
    assert service.pfr.phase == ResponsePhase.IDLE
    assert service.pfr.event is None
    sample = manager._samples[(ReferencePurpose.PFR_DELTA, "POI", INTERNAL)]
    assert sample.timestamp_ns == t0  # 未发布新样本
    assert not sample.is_valid_at(later)  # 参考过期，规划链因此闭锁


async def test_authority_revocation_exits_event_and_stops_contribution() -> None:
    service, source, manager, authority = make_service()
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.phase == ResponsePhase.RESPONDING
    old_event = service.pfr.event
    assert old_event is not None
    authority.take_out_of_service(ControlFunction.PFR)
    source.set("poi", "FREQ", 49.80, t0 + SECOND)
    await service.update(t0 + SECOND)
    assert service.pfr.phase == ResponsePhase.IDLE
    assert service.pfr.event is None
    sample = manager._samples[(ReferencePurpose.PFR_DELTA, "POI", INTERNAL)]
    assert sample.timestamp_ns == t0  # 撤销后不再发布
    # 重新投入后新扰动生成新事件，控制权撤销前的旧事件不得沿用。
    authority.put_in_service(ControlFunction.PFR, authorized=True)
    source.set("poi", "FREQ", 49.80, t0 + 2 * SECOND)
    await service.update(t0 + 2 * SECOND)
    assert service.pfr.event is not None
    assert service.pfr.event.event_id != old_event.event_id


async def test_recovery_hold_delays_reentry() -> None:
    service, source, manager, _ = make_service(recovery_s=2.0)
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    source.set("poi", "FREQ", 50.00, t0 + SECOND)
    await service.update(t0 + SECOND)
    assert service.pfr is not None and service.pfr.phase == ResponsePhase.RECOVERING
    # 恢复期内再次越限：不立即重新进入，增量保持零。
    source.set("poi", "FREQ", 49.90, t0 + 2 * SECOND)
    await service.update(t0 + 2 * SECOND)
    assert service.pfr.phase == ResponsePhase.RECOVERING
    reference = pfr_reference(manager, t0 + 2 * SECOND)
    assert reference is not None and reference.control.value == 0.0
    # 恢复期满后越限：作为新事件进入（时间戳精度到毫秒）。
    reenter = t0 + 3 * SECOND + 1_000_000
    source.set("poi", "FREQ", 49.90, reenter)
    await service.update(reenter)
    assert service.pfr.phase == ResponsePhase.RESPONDING
    assert service.pfr.event is not None
    assert service.pfr.event.entered_at_ns == reenter


async def test_reset_clears_event_history_across_task_restart() -> None:
    service, source, _manager, _ = make_service()
    t0 = 100 * SECOND
    source.set("poi", "FREQ", 49.90, t0)
    await service.update(t0)
    assert service.pfr is not None and service.pfr.event is not None
    first_event = service.pfr.event.event_id
    service.reset()
    assert service.pfr.phase == ResponsePhase.IDLE
    assert service.pfr.event is None
    source.set("poi", "FREQ", 49.90, t0 + SECOND)
    await service.update(t0 + SECOND)
    assert service.pfr.event is not None
    assert service.pfr.event.event_id != first_event


async def test_ir_event_requires_full_rocof_window_and_limits_output() -> None:
    service, source, manager, _ = make_service(pfr=False, ir=True)
    t0 = 100 * SECOND
    # 窗口未填满：不发布 IR 参考。
    source.set("poi", "FREQ", 50.00, t0)
    await service.update(t0)
    assert (ReferencePurpose.IR_DELTA, "POI", INTERNAL) not in manager._samples
    # 窗口内 0.4 Hz/s 下降：delta = -50 * (-0.4) = +20 → 限幅 +8 MW。
    for cycle in range(1, 5):
        ts = t0 + cycle * 250_000_000
        source.set("poi", "FREQ", 50.00 - 0.1 * cycle, ts)
        await service.update(ts)
    assert service.ir is not None and service.ir.phase == ResponsePhase.RESPONDING
    reference = manager.current(
        ReferencePurpose.IR_DELTA, "POI",
        now_ns=t0 + 4 * 250_000_000, authorized_sources=frozenset({INTERNAL}),
    )
    assert reference is not None
    assert reference.control.value == pytest.approx(8.0)


async def test_rocof_estimator_rejects_out_of_order_and_reports_window() -> None:
    estimator = RocofEstimator(window_s=1.0, min_samples=3)
    assert estimator.update(50.0, 1_000_000_000) is None
    assert estimator.update(49.9, 1_500_000_000) is None
    estimate = estimator.update(49.8, 2_000_000_000)
    assert estimate is not None
    assert estimate.value_hz_per_second == pytest.approx(-0.2)
    assert estimate.window_ns == 1_000_000_000
    assert estimate.samples == 3
    with pytest.raises(ValueError, match="out-of-order"):
        estimator.update(49.7, 1_900_000_000)
    with pytest.raises(ValueError, match="conflicting"):
        estimator.update(49.7, 2_000_000_000)
