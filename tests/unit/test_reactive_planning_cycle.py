"""P/Q 联合规划周期：有功与无功候选同时进入联合设备分配。"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from core.application.active_power_controller import ActivePowerController, ActivePowerCycle
from core.application.control_coordinator import ControlCoordinator
from core.application.control_laws import DirectControlLaw
from core.application.control_pipeline import ControlPipeline
from core.application.control_pipeline_factory import build_reactive_controller
from core.application.control_trajectory import PassthroughTrajectory
from core.application.joint_power_allocator import JointPowerAllocator
from core.application.port.planning_snapshot import PlanningSnapshot
from core.application.reactive_power_controller import ReactivePowerCycle
from core.application.read_only_planning_cycle import ReadOnlyPlanningCycle
from core.domain.avc import AvcMode
from core.domain.capability_snapshot import DeviceCapabilityRecord, DeviceCapabilitySnapshot
from core.domain.config import ControlPipelineConfig, CoordinationPolicyConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.device_capability import DevicePQCapability, DeviceTechnology


def value(
    quantity: ControlQuantity,
    amount: float,
    role: ControlValueRole,
    unit: str,
) -> ControlValue:
    return ControlValue(
        quantity, ControlScope.POI, "POI", role,
        ControlValueKind.ABSOLUTE, amount, unit,
    )


@dataclass
class JointSnapshotSource:
    async def acquire(self, task_id: str) -> PlanningSnapshot:
        now = time.time_ns()
        acquisition = now - 1_000_000
        device = DevicePQCapability(
            device_id="wtg", technology=DeviceTechnology.WIND,
            p_current=5.0, q_current=0.0,
            p_min=0.0, p_max=10.0, q_min=-5.0, q_max=5.0,
            s_max=10.0,
        )
        record = DeviceCapabilityRecord(
            capability=device, timestamp_ns=acquisition,
            generation=1, quality_good=True,
            p_authorized=True, q_authorized=True,
        )
        return PlanningSnapshot(
            active=ActivePowerCycle(
                base=value(ControlQuantity.ACTIVE_POWER, 6.0, ControlValueRole.REFERENCE, "MW"),
                measurement=value(ControlQuantity.ACTIVE_POWER, 5.0, ControlValueRole.MEASUREMENT, "MW"),
                dt_s=0.1, minimum=0.0, maximum=10.0,
            ),
            reactive=ReactivePowerCycle(
                mode=AvcMode.REACTIVE_POWER,
                reference=value(ControlQuantity.REACTIVE_POWER, 2.0, ControlValueRole.REFERENCE, "Mvar"),
                q_meas=value(ControlQuantity.REACTIVE_POWER, 0.0, ControlValueRole.MEASUREMENT, "Mvar"),
                dt_s=0.1, minimum=-5.0, maximum=5.0,
            ),
            devices=DeviceCapabilitySnapshot(
                records=(record,), generation=1,
                sampled_at_ns=acquisition, valid_until_ns=now + 5_000_000_000,
            ),
            acquired_at_ns=acquisition,
        )


def make_cycle(with_reactive: bool = True) -> ReadOnlyPlanningCycle:
    controller = ActivePowerController(
        pipeline=ControlPipeline(
            trajectory=PassthroughTrajectory(), law=DirectControlLaw(),
        ),
        coordinator=ControlCoordinator(CoordinationPolicyConfig()),
    )
    reactive = (
        {"pq": build_reactive_controller(AvcMode.REACTIVE_POWER, ControlPipelineConfig())}
        if with_reactive else {}
    )
    return ReadOnlyPlanningCycle(
        JointSnapshotSource(), {"pq": controller}, JointPowerAllocator(),
        maximum_age_ns=2_000_000_000, reactive_controllers=reactive,
    )


@pytest.mark.asyncio
async def test_joint_pq_cycle_produces_both_setpoints() -> None:
    result = await make_cycle().execute("pq")
    assert result.allocation.feasible
    assert len(result.allocation.active) == 1
    assert len(result.allocation.reactive) == 1
    assert result.allocation.active[0].value == pytest.approx(6.0)
    assert result.allocation.reactive[0].value == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_reactive_snapshot_without_controller_rejected() -> None:
    with pytest.raises(ValueError, match="no reactive planning controller"):
        await make_cycle(with_reactive=False).execute("pq")


@pytest.mark.asyncio
async def test_reactive_failure_resets_both_controllers() -> None:
    cycle = make_cycle()
    reactive_controller = cycle._reactive_controllers["pq"]
    config = ControlPipelineConfig.model_validate(
        {"law": {"algorithm": "pi", "kp": 1.0, "ki": 10.0}}
    )
    cycle._reactive_controllers["pq"] = build_reactive_controller(
        AvcMode.REACTIVE_POWER, config
    )
    # 离散 PI 从第二个周期开始累积积分。
    await cycle.execute("pq")
    await cycle.execute("pq")
    law = cycle._reactive_controllers["pq"].pipeline.law
    assert law._integral != 0.0
    # 快照来源注入非法目标后失败，两个控制器状态都必须复位。
    class BadSource(JointSnapshotSource):
        async def acquire(self, task_id: str) -> PlanningSnapshot:
            snapshot = await super().acquire(task_id)
            broken = ReactivePowerCycle(
                mode=AvcMode.REACTIVE_POWER,
                reference=value(ControlQuantity.REACTIVE_POWER, 2.0, ControlValueRole.REFERENCE, "Mvar"),
                q_meas=value(ControlQuantity.REACTIVE_POWER, 0.0, ControlValueRole.MEASUREMENT, "Mvar"),
                dt_s=-1.0, minimum=-5.0, maximum=5.0,
            )
            return PlanningSnapshot(
                active=snapshot.active, reactive=broken,
                devices=snapshot.devices, acquired_at_ns=snapshot.acquired_at_ns,
            )
    failing = ReadOnlyPlanningCycle(
        BadSource(), cycle._controllers, JointPowerAllocator(),
        maximum_age_ns=2_000_000_000,
        reactive_controllers=cycle._reactive_controllers,
    )
    with pytest.raises(ValueError):
        await failing.execute("pq")
    assert law._integral == 0.0
    assert reactive_controller is not None
