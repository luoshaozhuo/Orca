"""统一快照与 P/Q 联合分配测试。"""
from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from core.application.joint_power_allocator import JointPowerAllocator
from core.domain.capability_snapshot import DeviceCapabilityRecord, DeviceCapabilitySnapshot
from core.domain.device_capability import (
    DevicePQCapability, DeviceTechnology, StorageEnergyLimits,
)
from core.domain.device_ramp import DeviceRampLimits


def device(
    device_id: str, p: float = 0.0, q: float = 0.0,
    *, auto_p: bool = True, auto_q: bool = True, s: float = 10.0,
    technology: DeviceTechnology = DeviceTechnology.WIND,
) -> DevicePQCapability:
    return DevicePQCapability(
        device_id=device_id, technology=technology,
        p_current=p, q_current=q,
        p_min=-10.0, p_max=10.0, q_min=-10.0, q_max=10.0,
        s_max=s, p_auto=auto_p, q_auto=auto_q,
    )


def snapshot(*records: DeviceCapabilityRecord) -> DeviceCapabilitySnapshot:
    return DeviceCapabilitySnapshot(
        records=records, generation=7, sampled_at_ns=100,
        valid_until_ns=200,
    )


def record(item: DevicePQCapability, **kwargs) -> DeviceCapabilityRecord:
    kwargs.setdefault("quality_good", True)
    kwargs.setdefault("p_authorized", True)
    kwargs.setdefault("q_authorized", True)
    return DeviceCapabilityRecord(
        capability=item, timestamp_ns=90, generation=7, **kwargs,
    )


def solve(s: DeviceCapabilitySnapshot, p: float, q: float):
    return JointPowerAllocator().allocate(
        s, p_target=p, q_target=q, now_ns=110, maximum_age_ns=30,
    )


def test_joint_allocator_meets_both_targets() -> None:
    result = solve(
        snapshot(record(device("wind")), record(device("pv"))),
        12.0, 12.0,
    )
    assert result.feasible
    assert sum(item.value for item in result.active) == pytest.approx(12.0, abs=1e-6)
    assert sum(item.value for item in result.reactive) == pytest.approx(12.0, abs=1e-6)
    assert all(
        math.hypot(result.active[i].value, result.reactive[i].value) <= 10.000001
        for i in range(2)
    )


def test_impossible_joint_target_returns_no_setpoints() -> None:
    result = solve(snapshot(record(device("a")), record(device("b"))), 19, 19)
    assert not result.feasible
    assert result.active == result.reactive == ()


def test_manual_active_power_stays_fixed() -> None:
    result = solve(
        snapshot(
            record(device("manual", p=3, q=0, auto_p=False)),
            record(device("auto")),
        ),
        7.0, 4.0,
    )
    assert result.feasible
    assert [item.target for item in result.active] == ["auto"]
    assert len(result.reactive) == 2


def test_outdated_capability_is_rejected() -> None:
    s = snapshot(record(device("wtg")))
    with pytest.raises(ValueError, match="stale"):
        JointPowerAllocator().allocate(
            s, p_target=1, q_target=1,
            now_ns=160, maximum_age_ns=30,
        )


def test_mixed_generation_rejected() -> None:
    with pytest.raises(ValueError, match="mixed"):
        snapshot(DeviceCapabilityRecord(device("wtg"), 90, 6))


def test_soc_and_ramp_are_applied_before_joint_allocation() -> None:
    s = snapshot(
        record(
            device("storage", technology=DeviceTechnology.STORAGE),
            storage=StorageEnergyLimits(
                soc=0.2, soc_min=0.2, soc_max=0.8,
                energy_mwh=1.0, charge_efficiency=1.0,
                discharge_efficiency=1.0, horizon_s=1.0,
            ),
            p_ramp=DeviceRampLimits(
                up_per_s=1.0, down_per_s=1.0, horizon_s=1.0,
            ),
        )
    )
    assert s.bounded()[0].p_max == 0.0
    result = solve(s, 1.0, 0.0)
    assert not result.feasible
    assert not result.active


def test_empty_device_list_fails_closed() -> None:
    with pytest.raises(ValueError, match="requires device"):
        solve(snapshot(), 0.0, 0.0)


def test_bad_quality_device_blocks_joint_plan() -> None:
    s = snapshot(record(device("wtg"), quality_good=False))
    with pytest.raises(ValueError, match="bad capability quality"):
        solve(s, 0.0, 0.0)


def test_future_snapshot_is_rejected() -> None:
    s = snapshot(record(device("wtg")))
    with pytest.raises(ValueError, match="unavailable"):
        JointPowerAllocator().allocate(
            s, p_target=0.0, q_target=0.0,
            now_ns=99, maximum_age_ns=30,
        )


def test_nonfinite_target_is_rejected() -> None:
    s = snapshot(record(device("wtg")))
    with pytest.raises(ValueError, match="non-finite"):
        solve(s, float("nan"), 0.0)


def test_manual_pq_device_produces_no_setpoint() -> None:
    s = snapshot(record(device("manual", p=3.0, q=4.0, auto_p=False, auto_q=False)))
    result = solve(s, 3.0, 4.0)
    assert result.feasible
    assert not result.active and not result.reactive


def test_mixed_quality_snapshot_is_rejected_even_if_other_device_can_supply() -> None:
    s = snapshot(
        record(device("a")),
        record(device("b"), quality_good=False),
    )
    with pytest.raises(ValueError, match="bad capability quality"):
        solve(s, 1.0, 1.0)


def test_auto_storage_requires_fresh_soc_limits() -> None:
    with pytest.raises(ValueError, match="requires SOC"):
        record(device("ess", technology=DeviceTechnology.STORAGE))


def test_storage_horizon_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="horizons"):
        record(
            device("ess", technology=DeviceTechnology.STORAGE),
            storage=StorageEnergyLimits(
                soc=0.5, soc_min=0.1, soc_max=0.9,
                energy_mwh=1.0, charge_efficiency=0.9,
                discharge_efficiency=0.9, horizon_s=10.0,
            ),
            p_ramp=DeviceRampLimits(
                up_per_s=1.0, down_per_s=1.0, horizon_s=1.0,
            ),
        )


def test_deterministic_feasible_joint_cases() -> None:
    """固定随机种子构造已知可行的目标，验证联合投影守恒与能力圆。"""
    randomizer = random.Random(17)
    for _ in range(40):
        items = []
        p_goal = q_goal = 0.0
        for index in range(3):
            point = device(f"d{index}")
            items.append(record(point))
            angle = randomizer.uniform(-math.pi, math.pi)
            magnitude = randomizer.uniform(0.0, 8.0)
            p_goal += magnitude * math.cos(angle)
            q_goal += magnitude * math.sin(angle)
        result = solve(snapshot(*items), p_goal, q_goal)
        assert result.feasible, (p_goal, q_goal, result)
        assert abs(result.p_residual) <= 1e-6
        assert abs(result.q_residual) <= 1e-6
        for p, q in zip(result.active, result.reactive):
            assert math.hypot(p.value, q.value) <= 10.000001


def test_p_only_cannot_violate_fixed_reactive_output() -> None:
    s = snapshot(record(device("wtg", p=0.0, q=8.0)))
    result = JointPowerAllocator().allocate(
        s, p_target=8.0, q_target=None, now_ns=110, maximum_age_ns=30,
    )
    assert not result.feasible
    assert result.active == result.reactive == ()


def test_q_only_cannot_violate_fixed_active_output() -> None:
    s = snapshot(record(device("wtg", p=8.0, q=0.0)))
    result = JointPowerAllocator().allocate(
        s, p_target=None, q_target=8.0, now_ns=110, maximum_age_ns=30,
    )
    assert not result.feasible
    assert result.active == result.reactive == ()


def test_absent_write_authorization_prevents_power_setpoints() -> None:
    s = snapshot(
        DeviceCapabilityRecord(
            capability=device("wtg"),
            timestamp_ns=90,
            generation=7,
            quality_good=True,
        )
    )
    result = solve(s, 2.0, 0.0)
    assert not result.feasible
    assert not result.active
    assert not result.reactive


def test_one_channel_authorization_does_not_enable_other_channel() -> None:
    s = snapshot(
        DeviceCapabilityRecord(
            capability=device("wtg"),
            timestamp_ns=90,
            generation=7,
            quality_good=True,
            p_authorized=True,
            q_authorized=False,
        )
    )
    result = solve(s, 2.0, 0.0)
    assert result.feasible
    assert len(result.active) == 1
    assert not result.reactive


def test_joint_allocator_respects_active_power_weights() -> None:
    s = snapshot(
        record(replace(device("high"), p_weight=2.0)),
        record(replace(device("low"), p_weight=1.0)),
    )
    result = solve(s, 6.0, 0.0)
    assert result.feasible
    assert {item.target: item.value for item in result.active} == pytest.approx(
        {"high": 4.0, "low": 2.0}
    )


def test_joint_allocator_respects_reactive_power_weights() -> None:
    from dataclasses import replace

    s = snapshot(
        record(replace(device("high"), q_weight=3.0)),
        record(replace(device("low"), q_weight=1.0)),
    )
    result = solve(s, 0.0, 8.0)
    assert result.feasible
    assert {item.target: item.value for item in result.reactive} == pytest.approx(
        {"high": 6.0, "low": 2.0}
    )


def test_weighted_projection_respects_apparent_power_boundary() -> None:
    s = snapshot(
        record(replace(device("a"), p_weight=5.0, q_weight=1.0)),
        record(replace(device("b"), p_weight=1.0, q_weight=5.0)),
    )
    result = solve(s, 12.0, 12.0)
    assert result.feasible
    assert result.reason is None
    assert all(
        math.hypot(active.value, reactive.value) <= 10.000001
        for active, reactive in zip(result.active, result.reactive)
    )


def test_impossible_target_reports_failure_reason() -> None:
    result = solve(snapshot(record(device("wtg"))), 20.0, 0.0)
    assert not result.feasible
    assert result.reason == "active_power_outside_capability"
    assert result.iterations == 0
