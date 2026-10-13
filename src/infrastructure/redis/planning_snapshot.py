"""Redis 测点到冻结规划快照的显式映射，禁止依赖隐含测点命名。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
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
from core.domain.device_capability import DevicePQCapability, DeviceTechnology
from core.domain.reference import ReferenceSource


class MeasurementSource(Protocol):
    async def latest(
        self,
        pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """原子批次获取最新测点记录；带质量及设备采样时标。"""
        ...


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
    ) -> None:
        if generation < 0 or maximum_age_ns <= 0:
            raise ValueError("invalid planning generation or age")
        self._source = source
        self._references = references
        self._bindings = bindings
        self._generation = generation
        self._maximum_age_ns = maximum_age_ns

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
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate planning measurement mapping")
        records = await self._source.latest(pairs)
        now_ns = time.time_ns()
        measured: dict[tuple[str, str], float] = {}
        timestamps: list[int] = []
        for pair in pairs:
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
                ),
                generation=self._generation,
                quality_good=True,
                p_authorized=False,
                q_authorized=False,
            )
            for device in binding.devices
        )
        # 测点有效不代表现场控制权限；默认不得将采样成功升级为写入授权。
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
