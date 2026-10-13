"""只读周期从标准化参考、控制律到设备联合分配的集成测试。"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from core.application.active_power_controller import ActivePowerController, ActivePowerCycle
from core.application.control_coordinator import ControlCoordinator
from core.application.control_laws import DirectControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_trajectory import PassthroughTrajectory, RampTrajectory
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.port.planning_snapshot import PlanningSnapshot
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.domain.capability_snapshot import DeviceCapabilityRecord, DeviceCapabilitySnapshot
from core.domain.config import CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity, ControlScope, ControlValue, ControlValueKind, ControlValueRole,
)
from core.domain.device_capability import DevicePQCapability, DeviceTechnology


def value(amount: float, role: ControlValueRole) -> ControlValue:
    return ControlValue(
        ControlQuantity.ACTIVE_POWER, ControlScope.POI, "POI",
        role, ControlValueKind.ABSOLUTE, amount, "MW",
    )


@dataclass
class SnapshotSource:
    stale: bool = False
    invalid: bool = False

    async def acquire(self, task_id: str) -> PlanningSnapshot:
        assert task_id == "agc"
        now = time.time_ns()
        acquisition = now - (3_000_000_000 if self.stale else 1_000_000)
        device = DevicePQCapability(
            device_id="wtg", technology=DeviceTechnology.WIND,
            p_current=5.0, q_current=0.0,
            p_min=0.0, p_max=10.0, q_min=-5.0, q_max=5.0,
            s_max=10.0,
        )
        record = DeviceCapabilityRecord(
            capability=device, timestamp_ns=acquisition,
            generation=1, quality_good=not self.invalid,
            p_authorized=True, q_authorized=False,
        )
        return PlanningSnapshot(
            active=ActivePowerCycle(
                base=value(6.0, ControlValueRole.REFERENCE),
                measurement=value(5.0, ControlValueRole.MEASUREMENT),
                dt_s=0.1, minimum=0.0, maximum=10.0,
            ),
            devices=DeviceCapabilitySnapshot(
                records=(record,), generation=1,
                sampled_at_ns=acquisition, valid_until_ns=now + 5_000_000_000,
            ),
            acquired_at_ns=acquisition,
        )


def make_cycle(source: SnapshotSource) -> ReadOnlyPlanningCycle:
    controller = ActivePowerController(
        pipeline=ControlPipeline(
            trajectory=PassthroughTrajectory(), law=DirectControlLaw(),
        ),
        coordinator=ControlCoordinator(CoordinationPolicyConfig()),
    )
    return ReadOnlyPlanningCycle(
        source, {"agc": controller}, JointPowerAllocator(),
        maximum_age_ns=2_000_000_000,
    )


@pytest.mark.asyncio
async def test_read_only_cycle_generates_candidates_without_commander() -> None:
    cycle = make_cycle(SnapshotSource())
    result = await cycle.execute("agc")
    assert result.allocation.feasible
    assert len(result.allocation.active) == 1
    assert result.allocation.active[0].value == pytest.approx(6.0)
    assert result.allocation.reactive == ()


@pytest.mark.asyncio
async def test_stale_input_rejected_before_control_calculation() -> None:
    cycle = make_cycle(SnapshotSource(stale=True))
    with pytest.raises(ValueError, match="stale"):
        await cycle.execute("agc")


@pytest.mark.asyncio
async def test_bad_quality_rejected_during_allocation() -> None:
    cycle = make_cycle(SnapshotSource(invalid=True))
    with pytest.raises(ValueError, match="bad capability quality"):
        await cycle.execute("agc")


@pytest.mark.asyncio
async def test_unknown_planning_controller_rejected() -> None:
    cycle = make_cycle(SnapshotSource())
    with pytest.raises(ValueError, match="no planning controller"):
        await cycle.execute("missing")


@pytest.mark.asyncio
async def test_ramp_first_cycle_initializes_from_valid_feedback() -> None:
    service = make_cycle(SnapshotSource())
    controller = service._controllers["agc"]
    controller.pipeline.trajectory = RampTrajectory(
        up_per_s=1.0,
        down_per_s=1.0,
    )
    result = await service.execute("agc")
    # 0.1 s 内参考从实测 5 MW 朝 6 MW 变化至 5.1 MW。
    assert result.allocation.active[0].value == pytest.approx(5.1)


@pytest.mark.asyncio
async def test_stale_cycle_does_not_initialize_ramp() -> None:
    service = make_cycle(SnapshotSource(stale=True))
    controller = service._controllers["agc"]
    ramp = RampTrajectory(up_per_s=1.0, down_per_s=1.0)
    controller.pipeline.trajectory = ramp
    with pytest.raises(ValueError, match="stale"):
        await service.execute("agc")
    assert not ramp.initialized
