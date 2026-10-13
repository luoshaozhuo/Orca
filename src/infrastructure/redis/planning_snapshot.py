"""Redis 测点到冻结规划快照的显式映射，禁止依赖隐含测点命名。"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from core.application.active_power_reference import active_power_cycle_from_references
from core.application.port.planning_snapshot import PlanningSnapshot
from core.application.reactive_power_controller import ReactivePowerCycle
from core.application.reference_manager import ReferenceManager
from core.domain.agc import MODE_PURPOSE as AGC_MODE_PURPOSE
from core.domain.agc import AgcMode
from core.domain.avc import MODE_PURPOSE, AvcMode
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
    DevicePQCapability,
    DeviceTechnology,
    StorageEnergyLimits,
)
from core.domain.device_ramp import DeviceRampLimits
from core.domain.reference import (
    ControlReference,
    ReferencePurpose,
    ReferenceSource,
)

# 参考用途到控制量/数值语义/工程单位的映射；不允许由点位拼写推断。
_PURPOSE_VALUE_META: dict[
    ReferencePurpose,
    tuple[ControlQuantity, ControlValueKind | None, str],
] = {
    ReferencePurpose.ACTIVE_BASE: (
        ControlQuantity.ACTIVE_POWER, ControlValueKind.ABSOLUTE, "MW",
    ),
    # ACTIVE_LIMIT 的上限/下限语义由绑定显式给出，不允许默认。
    ReferencePurpose.ACTIVE_LIMIT: (
        ControlQuantity.ACTIVE_POWER, None, "MW",
    ),
    ReferencePurpose.PFR_DELTA: (
        ControlQuantity.ACTIVE_POWER, ControlValueKind.DELTA, "MW",
    ),
    ReferencePurpose.IR_DELTA: (
        ControlQuantity.ACTIVE_POWER, ControlValueKind.DELTA, "MW",
    ),
    ReferencePurpose.REACTIVE_BASE: (
        ControlQuantity.REACTIVE_POWER, ControlValueKind.ABSOLUTE, "Mvar",
    ),
    ReferencePurpose.VOLTAGE_BASE: (
        ControlQuantity.VOLTAGE, ControlValueKind.ABSOLUTE, "kV",
    ),
    ReferencePurpose.POWER_FACTOR: (
        ControlQuantity.POWER_FACTOR, ControlValueKind.ABSOLUTE, "1",
    ),
}


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
    kind: ControlValueKind | None = None

    def __post_init__(self) -> None:
        if not self.resource_id or not self.point_id or self.valid_for_ns <= 0:
            raise ValueError("invalid external planning reference binding")
        limit_kinds = {ControlValueKind.UPPER_LIMIT, ControlValueKind.LOWER_LIMIT}
        if self.purpose == ReferencePurpose.ACTIVE_LIMIT:
            if self.kind not in limit_kinds:
                raise ValueError("ACTIVE_LIMIT binding requires an explicit limit kind")
        elif self.kind is not None:
            raise ValueError("limit kind is only valid for ACTIVE_LIMIT bindings")


@dataclass(frozen=True, slots=True)
class ReactivePlanningBinding:
    """AVC 无功规划的 POI 测点与参考映射；设备能力复用有功绑定。"""

    target: str
    mode: AvcMode
    poi_device_id: str
    poi_reactive_point: str
    minimum: float
    maximum: float
    authorized_sources: frozenset[ReferenceSource]
    poi_voltage_point: str | None = None
    reference_inputs: tuple[PlanningReferenceBinding, ...] = ()

    def __post_init__(self) -> None:
        if not self.target or not self.poi_device_id or not self.poi_reactive_point:
            raise ValueError("reactive planning POI binding is required")
        if not self.authorized_sources:
            raise ValueError("reactive planning must explicitly authorize sources")
        if not all(math.isfinite(v) for v in (self.minimum, self.maximum)):
            raise ValueError("reactive planning limits must be finite")
        if self.minimum > self.maximum:
            raise ValueError("reactive planning limits are inverted")
        allowed = {
            ReferencePurpose.REACTIVE_BASE,
            ReferencePurpose.VOLTAGE_BASE,
            ReferencePurpose.POWER_FACTOR,
        }
        keys = [(ref.purpose, ref.source) for ref in self.reference_inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate reactive planning reference")
        for reference in self.reference_inputs:
            if reference.purpose not in allowed:
                raise ValueError("unsupported reactive planning reference purpose")
            if reference.source not in self.authorized_sources:
                raise ValueError("reactive planning reference source is not authorized")
        required = MODE_PURPOSE[self.mode]
        if not any(ref.purpose == required for ref in self.reference_inputs):
            raise ValueError(f"{self.mode}: missing {required.value} reference binding")
        if self.mode == AvcMode.VOLTAGE and self.poi_voltage_point is None:
            raise ValueError("VOLTAGE mode requires a POI voltage measurement")


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
    mode: AgcMode = AgcMode.SETPOINT
    poi_available_point: str | None = None
    reference_inputs: tuple[PlanningReferenceBinding, ...] = ()
    active_functions: frozenset[CoordinationFunction] = frozenset()
    maximum_skew_ns: int = 500_000_000
    reactive: ReactivePlanningBinding | None = None

    def __post_init__(self) -> None:
        if not self.target or not self.poi_device_id or not self.poi_active_point:
            raise ValueError("planning POI binding is required")
        if not self.devices or len({d.device_id for d in self.devices}) != len(self.devices):
            raise ValueError("planning requires unique devices")
        if not self.authorized_sources:
            raise ValueError("planning must explicitly authorize reference sources")
        keys = [(ref.purpose, ref.source) for ref in self.reference_inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate external planning reference")
        if any(ref.source not in self.authorized_sources for ref in self.reference_inputs):
            raise ValueError("external reference source is not authorized")
        if self.maximum_skew_ns <= 0:
            raise ValueError("planning maximum sample skew must be positive")
        if not all(math.isfinite(v) for v in (self.minimum, self.maximum, self.dt_s)):
            raise ValueError("planning limits must be finite")
        if self.minimum > self.maximum or self.dt_s <= 0:
            raise ValueError("invalid planning target limits or period")
        if self.mode == AgcMode.LIMIT and self.poi_available_point is None:
            raise ValueError("LIMIT mode requires an available-power measurement")
        if self.mode != AgcMode.LIMIT and self.poi_available_point is not None:
            raise ValueError("available-power measurement is only used in LIMIT mode")


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
        can_contribute: Callable[[str, CoordinationFunction], bool] | None = None,
    ) -> None:
        if generation < 0 or maximum_age_ns <= 0:
            raise ValueError("invalid planning generation or age")
        self._source = source
        self._references = references
        self._bindings = bindings
        self._generation = generation
        self._maximum_age_ns = maximum_age_ns
        self._can_plan = can_plan
        self._can_contribute = can_contribute
        self._last_reference: dict[tuple[str, ReferencePurpose, ReferenceSource], tuple[int, float]] = {}

    @staticmethod
    def _value(record: dict[str, Any], key: tuple[str, str], now_ns: int, max_age: int) -> tuple[float, int]:
        if str(record.get("quality", "")).lower() != "good":
            raise ValueError(f"{key}: bad measurement quality")
        number = record.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{key}: numeric measurement required")  # noqa: TRY004 - existing validation API
        reading = float(number)
        if not math.isfinite(reading):
            raise ValueError(f"{key}: non-finite measurement")
        try:
            timestamp = datetime.fromisoformat(str(record["timestamp"]))
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
        reactive = binding.reactive
        pairs = [(binding.poi_device_id, binding.poi_active_point)]
        if binding.poi_available_point is not None:
            pairs.append((binding.poi_device_id, binding.poi_available_point))
        for device in binding.devices:
            pairs.extend(((device.device_id, device.p_point), (device.device_id, device.q_point)))
            if device.storage is not None:
                pairs.append((device.device_id, device.storage.soc_point))
        if reactive is not None:
            pairs.append((reactive.poi_device_id, reactive.poi_reactive_point))
            if reactive.poi_voltage_point is not None:
                pairs.append((reactive.poi_device_id, reactive.poi_voltage_point))
        measurement_pairs = tuple(pairs)
        reference_inputs = tuple(binding.reference_inputs) + (
            tuple(reactive.reference_inputs) if reactive is not None else ()
        )
        for reference in reference_inputs:
            pairs.append((reference.resource_id, reference.point_id))
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate planning measurement mapping")
        records = await self._source.latest(pairs)
        now_ns = time.time_ns()
        measured: dict[tuple[str, str], float] = {}
        stamps: dict[tuple[str, str], int] = {}
        for pair in measurement_pairs:
            record = records.get(pair)
            if record is None:
                raise ValueError(f"{pair}: missing measurement")
            value, timestamp_ns = self._value(
                record, pair, now_ns, self._maximum_age_ns,
            )
            measured[pair] = value
            stamps[pair] = timestamp_ns
        timestamps = list(stamps.values())
        if max(timestamps) - min(timestamps) > binding.maximum_skew_ns:
            raise ValueError("planning measurements have excessive time skew")
        pending: list[tuple[tuple[str, ReferencePurpose, ReferenceSource], ControlReference]] = []
        for reference in reference_inputs:
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
            quantity, kind, unit = _PURPOSE_VALUE_META[reference.purpose]
            if kind is None:
                assert reference.kind is not None
                kind = reference.kind
            event_id = raw.get("event_id")
            if event_id is not None and (not isinstance(event_id, str) or not event_id):
                raise ValueError(f"{key}: invalid reference event id")
            sample = ControlReference(
                purpose=reference.purpose,
                source=reference.source,
                control=ControlValue(
                    quantity, ControlScope.POI, binding.target,
                    ControlValueRole.REFERENCE, kind, number, unit,
                ),
                timestamp_ns=stamp_ns,
                valid_until_ns=stamp_ns + reference.valid_for_ns,
                generation=self._generation,
                sequence_id=stamp_ns,
                event_id=event_id,
            )
            pending.append((sample_key, sample))
        self._references.publish_many(tuple(sample for _, sample in pending))
        for pending_key, sample in pending:
            self._last_reference[pending_key] = (sample.timestamp_ns, sample.control.value)

        poi = ControlValue(
            ControlQuantity.ACTIVE_POWER, ControlScope.POI, binding.target,
            ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE,
            measured[pairs[0]], "MW",
        )
        for function in binding.active_functions:
            if self._can_contribute is None or not self._can_contribute(task_id, function):
                raise ValueError(
                    f"{function.value}: control contribution not authorized"
                )
        base_purpose = AGC_MODE_PURPOSE[binding.mode]
        feedforward = measured[pairs[0]]
        if binding.mode == AgcMode.LIMIT:
            assert binding.poi_available_point is not None
            feedforward = measured[(binding.poi_device_id, binding.poi_available_point)]
        cycle = active_power_cycle_from_references(
            self._references,
            target=binding.target,
            measurement=poi,
            now_ns=now_ns,
            authorized_sources=binding.authorized_sources,
            dt_s=binding.dt_s,
            minimum=binding.minimum,
            maximum=binding.maximum,
            feedforward=feedforward,
            active_functions=binding.active_functions,
            required_generation=self._generation,
            base_purpose=base_purpose,
        )
        purposes = [base_purpose]
        if CoordinationFunction.PFR in binding.active_functions:
            purposes.append(ReferencePurpose.PFR_DELTA)
        if CoordinationFunction.IR in binding.active_functions:
            purposes.append(ReferencePurpose.IR_DELTA)
        expirations: list[int] = []
        for purpose in purposes:
            selected = self._references.current(
                purpose,
                binding.target,
                now_ns=now_ns,
                authorized_sources=binding.authorized_sources,
            )
            if selected is None:
                raise ValueError(f"{purpose.value}: reference expired before snapshot")
            expirations.append(selected.valid_until_ns)

        reactive_cycle: ReactivePowerCycle | None = None
        reactive_expiration: int | None = None
        if reactive is not None:
            q_meas = ControlValue(
                ControlQuantity.REACTIVE_POWER, ControlScope.POI, reactive.target,
                ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE,
                measured[(reactive.poi_device_id, reactive.poi_reactive_point)], "Mvar",
            )
            u_meas = None
            if reactive.poi_voltage_point is not None:
                u_meas = ControlValue(
                    ControlQuantity.VOLTAGE, ControlScope.POI, reactive.target,
                    ControlValueRole.MEASUREMENT, ControlValueKind.ABSOLUTE,
                    measured[(reactive.poi_device_id, reactive.poi_voltage_point)], "kV",
                )
            purpose = MODE_PURPOSE[reactive.mode]
            selected = self._references.current(
                purpose,
                reactive.target,
                now_ns=now_ns,
                authorized_sources=reactive.authorized_sources,
            )
            if selected is None:
                raise ValueError(f"{purpose.value}: reference expired before snapshot")
            reactive_expiration = selected.valid_until_ns
            reactive_cycle = ReactivePowerCycle(
                mode=reactive.mode,
                reference=selected.control,
                q_meas=q_meas,
                dt_s=binding.dt_s,
                minimum=reactive.minimum,
                maximum=reactive.maximum,
                u_meas=u_meas,
                p_meas=poi,
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
                    stamps[(device.device_id, device.p_point)],
                    stamps[(device.device_id, device.q_point)],
                    *(
                        (stamps[(device.device_id, device.storage.soc_point)],)
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
        if reactive_expiration is not None:
            expirations.append(reactive_expiration)
        return PlanningSnapshot(
            active=cycle,
            reactive=reactive_cycle,
            devices=DeviceCapabilitySnapshot(
                records=devices,
                generation=self._generation,
                sampled_at_ns=max(timestamps),
                valid_until_ns=max(timestamps) + self._maximum_age_ns,
            ),
            acquired_at_ns=max(timestamps),
            expires_at_ns=min(
                min(expirations), max(timestamps) + self._maximum_age_ns,
            ),
        )
