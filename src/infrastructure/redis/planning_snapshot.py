"""Redis 测点到冻结规划快照的显式映射，禁止依赖隐含测点命名。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from core.application.active_power_reference import active_power_cycle_from_references
from core.application.port.planning_snapshot import PlanningSnapshot
from core.application.reference_manager import ReferenceManager
from core.domain.capability_snapshot import (
    DeviceCapabilityRecord,
    DeviceCapabilitySnapshot,
)
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import CoordinationFunction
from core.domain.device_capability import (
    DevicePQCapability, DeviceTechnology, StorageEnergyLimits,
)
from core.domain.device_ramp import DeviceRampLimits
from core.domain.reference import (
    ControlReference, ReferencePurpose, ReferenceSource,
)


class MeasurementSource(Protocol):
    async def latest(
        self,
        pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """原子批次获取最新测点记录；带质量及设备采样时标。"""
        ...


@dataclass(frozen=True, slots=True)
class StoragePlanningBinding:
    soc_point: str
    soc_min: float
    soc_max: float
    energy_mwh: float
    charge_efficiency: float
    discharge_efficiency: float


@dataclass(frozen=True, slots=True)
class DevicePlanningBinding:
    """设备点位及经设备制造商或工程设计确定的静态能力。"""

    device_id: str
    p_point: str
    q_point: str
    technology: DeviceTechnology
    p_min: float
    p_max: float
    q_min: float
    q_max: float
    s_max: float
    p_auto: bool = False
    q_auto: bool = False
    p_weight: float = 1.0
    q_weight: float = 1.0
    p_ramp_up: float | None = None
    p_ramp_down: float | None = None
    q_ramp_up: float | None = None
    q_ramp_down: float | None = None
    storage: StoragePlanningBinding | None = None


@dataclass(frozen=True, slots=True)
class PlanningReferenceBinding:
    purpose: ReferencePurpose
    source: ReferenceSource
    resource_id: str
    point_id: str
    valid_for_ns: int


@dataclass(frozen=True, slots=True)
class ActivePlanningBinding:
    """任务的 POI 测点及设备映射，来源授权只能由调用方提供。"""

    target: str
    poi_device_id: str
    poi_active_point: str
    minimum: float
    maximum: float
    dt_s: float
    devices: tuple[DevicePlanningBinding, ...]
    authorized_sources: frozenset[ReferenceSource]
    reference_inputs: tuple[PlanningReferenceBinding, ...] = ()
    active_functions: frozenset[CoordinationFunction] = frozenset()
    maximum_skew_ns: int = 500_000_000

    def __post_init__(self) -> None:
        if not self.target or not self.poi_device_id or not self.poi_active_point:
            raise ValueError("planning POI binding is required")
        if not self.devices or len({d.device_id for d in self.devices}) != len(self.devices):
            raise ValueError("planning requires unique devices")
        if not self.authorized_sources:
            raise ValueError("planning must explicitly authorize reference sources")
        if self.maximum_skew_ns <= 0:
            raise ValueError("planning maximum sample skew must be positive")
        if not all(math.isfinite(v) for v in (self.minimum, self.maximum, self.dt_s)):
            raise ValueError("planning limits must be finite")
        if self.minimum > self.maximum or self.dt_s <= 0:
            raise ValueError("invalid planning target limits or period")


class RedisPlanningSnapshotSource:
    """单次批量读取，验证所有被使用测点的质量、时间和数值。"""

    def __init__(
        self,
        source: MeasurementSource,
        references: ReferenceManager,
        bindings: dict[str, ActivePlanningBinding],
        *,
        generation: int,
        maximum_age_ns: int,
        can_plan: Callable[[str, str, ControlQuantity], bool] | None = None,
    ) -> None:
        if generation < 0 or maximum_age_ns <= 0:
            raise ValueError("invalid planning generation or age")
        self._source = source
        self._references = references
        self._bindings = bindings
        self._generation = generation
        self._maximum_age_ns = maximum_age_ns
        self._can_plan = can_plan
        self._last_reference: dict[tuple[str, ReferencePurpose, ReferenceSource], tuple[int, float]] = {}

    @staticmethod
    def _value(record: dict[str, Any], key: tuple[str, str], now_ns: int, max_age: int) -> tuple[float, int]:
        if str(record.get("quality", "")).lower() != "good":
            raise ValueError(f"{key}: bad measurement quality")
        number = record.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{key}: numeric measurement required")
        reading = float(number)
        if not math.isfinite(reading):
            raise ValueError(f"{key}: non-finite measurement")
        try:
            timestamp = datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("naive measurement timestamp")
            stamp_ns = int(timestamp.timestamp() * 1_000_000_000)
        except (KeyError, ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"{key}: invalid measurement timestamp") from error
        if stamp_ns > now_ns or now_ns - stamp_ns > max_age:
            raise ValueError(f"{key}: stale or future measurement")
        return reading, stamp_ns

    async def acquire(self, task_id: str) -> PlanningSnapshot:
        binding = self._bindings[task_id]
        pairs = [(binding.poi_device_id, binding.poi_active_point)]
        for device in binding.devices:
            pairs.extend(((device.device_id, device.p_point), (device.device_id, device.q_point)))
            if device.storage is not None:
                pairs.append((device.device_id, device.storage.soc_point))
        measurement_pairs = tuple(pairs)
        for reference in binding.reference_inputs:
            pairs.append((reference.resource_id, reference.point_id))
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate planning measurement mapping")
        records = await self._source.latest(pairs)
        now_ns = time.time_ns()
        measured: dict[tuple[str, str], float] = {}
        timestamps: list[int] = []
        for pair in measurement_pairs:
            record = records.get(pair)
            if record is None:
                raise ValueError(f"{pair}: missing measurement")
            value, timestamp_ns = self._value(
                record, pair, now_ns, self._maximum_age_ns,
            )
            measured[pair] = value
            timestamps.append(timestamp_ns)
        if max(timestamps) - min(timestamps) > binding.maximum_skew_ns:
            raise ValueError("planning measurements have excessive time skew")
        for reference in binding.reference_inputs:
            key = (reference.resource_id, reference.point_id)
            raw = records.get(key)
            if raw is None:
                raise ValueError(f"{key}: missing planning reference")
            number, stamp_ns = self._value(
                raw, key, now_ns, reference.valid_for_ns,
            )
            sample_key = (task_id, reference.purpose, reference.source)
            previous = self._last_reference.get(sample_key)
            if previous is not None:
                if stamp_ns < previous[0] or (
                    stamp_ns == previous[0] and number != previous[1]
                ):
                    raise ValueError("planning reference update is out of order")
                if stamp_ns == previous[0]:
                    continue
            kind = (
                ControlValueKind.ABSOLUTE
                if reference.purpose == ReferencePurpose.ACTIVE_BASE
                else ControlValueKind.DELTA
            )
            sample = ControlReference(
                purpose=reference.purpose,
                source=reference.source,
                control=ControlValue(
                    ControlQuantity.ACTIVE_POWER, ControlScope.POI, binding.target,
                    ControlValueRole.REFERENCE, kind, number, "MW",
                ),
                timestamp_ns=stamp_ns,
                valid_until_ns=stamp_ns + reference.valid_for_ns,
                generation=self._generation,
                sequence_id=stamp_ns,
            )
            self._references.publish(sample)
            self._last_reference[sample_key] = (stamp_ns, number)

        poi = ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.POI, binding.target,
            ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE,
            measured[pairs[0]], "MW",
        )
        cycle = active_power_cycle_from_references(
            self._references,
            target=binding.target,
            measurement=poi,
            now_ns=now_ns,
            authorized_sources=binding.authorized_sources,
            dt_s=binding.dt_s,
            minimum=binding.minimum,
            maximum=binding.maximum,
            feedforward=measured[pairs[0]],
            active_functions=binding.active_functions,
            required_generation=self._generation,
        )
        devices = tuple(
            DeviceCapabilityRecord(
                capability=DevicePQCapability(
                    device_id=device.device_id,
                    technology=device.technology,
                    p_current=measured[(device.device_id, device.p_point)],
                    q_current=measured[(device.device_id, device.q_point)],
                    p_min=device.p_min,
                    p_max=device.p_max,
                    q_min=device.q_min,
                    q_max=device.q_max,
                    s_max=device.s_max,
                    p_auto=device.p_auto,
                    q_auto=device.q_auto,
                    p_weight=device.p_weight,
                    q_weight=device.q_weight,
                ),
                timestamp_ns=min(
                    timestamps[pairs.index((device.device_id, device.p_point))],
                    timestamps[pairs.index((device.device_id, device.q_point))],
                    *(
                        (timestamps[pairs.index((device.device_id, device.storage.soc_point))],)
                        if device.storage is not None else ()
                    ),
                ),
                generation=self._generation,
                storage=(
                    StorageEnergyLimits(
                        soc=measured[(device.device_id, device.storage.soc_point)],
                        soc_min=device.storage.soc_min,
                        soc_max=device.storage.soc_max,
                        energy_mwh=device.storage.energy_mwh,
                        charge_efficiency=device.storage.charge_efficiency,
                        discharge_efficiency=device.storage.discharge_efficiency,
                        horizon_s=binding.dt_s,
                    )
                    if device.storage is not None else None
                ),
                p_ramp=(
                    DeviceRampLimits(
                        device.p_ramp_up, device.p_ramp_down, binding.dt_s,
                    )
                    if device.p_ramp_up is not None and device.p_ramp_down is not None
                    else None
                ),
                q_ramp=(
                    DeviceRampLimits(
                        device.q_ramp_up, device.q_ramp_down, binding.dt_s,
                    )
                    if device.q_ramp_up is not None and device.q_ramp_down is not None
                    else None
                ),
                quality_good=True,
                p_authorized=(
                    self._can_plan is not None
                    and self._can_plan(task_id, device.device_id, ControlQuantity.ACTIVE_POWER)
                ),
                q_authorized=(
                    self._can_plan is not None
                    and self._can_plan(task_id, device.device_id, ControlQuantity.REACTIVE_POWER)
                ),
            )
            for device in binding.devices
        )
        # 测点有效不代表控制授权；未显式注入权限检查则一律拒绝自动分配。
        return PlanningSnapshot(
            active=cycle,
            devices=DeviceCapabilitySnapshot(
                records=devices,
                generation=self._generation,
                sampled_at_ns=max(timestamps),
                valid_until_ns=max(timestamps) + self._maximum_age_ns,
            ),
            acquired_at_ns=max(timestamps),
        )
