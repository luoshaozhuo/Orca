"""频率测量、RoCoF 滑窗估计与 PFR/IR 事件状态机。

事件生命周期：IDLE → RESPONDING → RECOVERING → IDLE。
扰动持续期间事件标识与进入基线保持不变；退出后按恢复时间闭合事件。
任何输入失效都使状态机复位并停止发布内部参考，不沿用旧事件。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from core.application.internal_reference import InternalReferenceService
from core.application.reference_generators import InertiaParameters, PFRParameters
from core.domain.control_authority import ControlFunction
from core.domain.reference import ReferencePurpose, ReferenceSource


class ResponsePhase(StrEnum):
    """控制功能响应阶段；投入不等于正在输出。"""

    IDLE = "IDLE"
    RESPONDING = "RESPONDING"
    RECOVERING = "RECOVERING"


class MeasurementSource(Protocol):
    async def latest(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        ...


@dataclass(frozen=True, slots=True)
class RocofEstimate:
    """带时标、时间窗与样本数的频率变化率估计。"""

    value_hz_per_second: float
    timestamp_ns: int
    window_ns: int
    samples: int


@dataclass(slots=True)
class RocofEstimator:
    """滑窗最小二乘拟合 RoCoF；窗口不足或时序异常即无效。"""

    window_s: float
    min_samples: int = 3
    _samples: list[tuple[int, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not math.isfinite(self.window_s) or self.window_s <= 0:
            raise ValueError("RoCoF window must be positive and finite")
        if self.min_samples < 3:
            raise ValueError("RoCoF fit requires at least three samples")

    def reset(self) -> None:
        self._samples.clear()

    def update(self, frequency_hz: float, timestamp_ns: int) -> RocofEstimate | None:
        """窗口未填满返回 None；时间倒挂或同刻异值拒绝。"""
        if not math.isfinite(frequency_hz):
            raise ValueError("frequency must be finite")
        if timestamp_ns < 0:
            raise ValueError("timestamp must be nonnegative")
        if self._samples:
            last_ts, last_value = self._samples[-1]
            if timestamp_ns < last_ts:
                raise ValueError("out-of-order frequency sample")
            if timestamp_ns == last_ts:
                if frequency_hz != last_value:
                    raise ValueError("conflicting frequency sample")
                return self._estimate()
        self._samples.append((timestamp_ns, frequency_hz))
        horizon = timestamp_ns - int(self.window_s * 1_000_000_000)
        self._samples = [item for item in self._samples if item[0] >= horizon]
        return self._estimate()

    def _estimate(self) -> RocofEstimate | None:
        if len(self._samples) < self.min_samples:
            return None
        span_ns = self._samples[-1][0] - self._samples[0][0]
        if span_ns < int(self.window_s * 1_000_000_000) // 2:
            return None
        t_mean = sum(item[0] for item in self._samples) / len(self._samples)
        f_mean = sum(item[1] for item in self._samples) / len(self._samples)
        numerator = sum(
            (ts - t_mean) * (value - f_mean) for ts, value in self._samples
        )
        denominator = sum((ts - t_mean) ** 2 for ts, _ in self._samples)
        if denominator <= 0:
            return None
        # 斜率单位为 Hz/ns，换算为 Hz/s。
        slope = numerator / denominator * 1_000_000_000
        return RocofEstimate(
            value_hz_per_second=slope,
            timestamp_ns=self._samples[-1][0],
            window_ns=span_ns,
            samples=len(self._samples),
        )


@dataclass(frozen=True, slots=True)
class ResponseEvent:
    """事件进入时刻冻结的标识与基线；持续期间不重新冻结。"""

    event_id: str
    entered_at_ns: int
    generation: int
    frozen_base_mw: float | None
    frozen_base_timestamp_ns: int | None


@dataclass(slots=True)
class PfrEventMachine:
    """一次调频事件：死区外进入、死区内退出、恢复后闭合。"""

    parameters: PFRParameters
    recovery_s: float = 0.0
    phase: ResponsePhase = ResponsePhase.IDLE
    event: ResponseEvent | None = None
    _recover_until_ns: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.recovery_s) or self.recovery_s < 0:
            raise ValueError("PFR recovery time must be finite and nonnegative")

    def reset(self) -> None:
        self.phase = ResponsePhase.IDLE
        self.event = None
        self._recover_until_ns = 0

    def update(
        self,
        frequency_hz: float,
        timestamp_ns: int,
        *,
        generation: int,
        frozen_base: tuple[float, int] | None,
    ) -> tuple[float, str | None]:
        """返回（功率增量 MW，有效事件标识）；IDLE/RECOVERING 增量为零。"""
        if not math.isfinite(frequency_hz):
            raise ValueError("frequency must be finite")
        deviation = frequency_hz - self.parameters.nominal_frequency_hz
        outside = abs(deviation) > self.parameters.deadband_hz
        if self.phase == ResponsePhase.IDLE:
            if not outside:
                return 0.0, None
            base_value, base_ts = frozen_base if frozen_base is not None else (None, None)
            self.event = ResponseEvent(
                event_id=f"pfr-{timestamp_ns}",
                entered_at_ns=timestamp_ns,
                generation=generation,
                frozen_base_mw=base_value,
                frozen_base_timestamp_ns=base_ts,
            )
            self.phase = ResponsePhase.RESPONDING
        elif self.phase == ResponsePhase.RECOVERING:
            if timestamp_ns < self._recover_until_ns:
                return 0.0, None
            self.phase = ResponsePhase.IDLE
            if outside:
                return self.update(
                    frequency_hz, timestamp_ns,
                    generation=generation, frozen_base=frozen_base,
                )
            return 0.0, None
        # RESPONDING：扰动持续不重建事件、不重新冻结基线。
        if outside:
            assert self.event is not None
            magnitude = max(abs(deviation) - self.parameters.deadband_hz, 0.0)
            change = -math.copysign(self.parameters.gain_mw_per_hz * magnitude, deviation)
            delta = max(
                -self.parameters.max_decrease_mw,
                min(self.parameters.max_increase_mw, change),
            )
            return delta, self.event.event_id
        # 频率返回死区：事件进入恢复阶段，增量立即归零。
        self.phase = ResponsePhase.RECOVERING
        self.event = None
        self._recover_until_ns = timestamp_ns + int(self.recovery_s * 1_000_000_000)
        return 0.0, None


@dataclass(slots=True)
class IrEventMachine:
    """惯量响应事件：|RoCoF| 越限进入、回落退出、恢复后闭合。"""

    parameters: InertiaParameters
    threshold_hz_per_s: float
    recovery_s: float = 0.0
    phase: ResponsePhase = ResponsePhase.IDLE
    event: ResponseEvent | None = None
    _recover_until_ns: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold_hz_per_s) or self.threshold_hz_per_s <= 0:
            raise ValueError("IR RoCoF threshold must be positive and finite")
        if not math.isfinite(self.recovery_s) or self.recovery_s < 0:
            raise ValueError("IR recovery time must be finite and nonnegative")

    def reset(self) -> None:
        self.phase = ResponsePhase.IDLE
        self.event = None
        self._recover_until_ns = 0

    def update(
        self,
        rocof_hz_per_second: float,
        timestamp_ns: int,
        *,
        generation: int,
        frozen_base: tuple[float, int] | None,
    ) -> tuple[float, str | None]:
        if not math.isfinite(rocof_hz_per_second):
            raise ValueError("RoCoF must be finite")
        outside = abs(rocof_hz_per_second) > self.threshold_hz_per_s
        if self.phase == ResponsePhase.IDLE:
            if not outside:
                return 0.0, None
            base_value, base_ts = frozen_base if frozen_base is not None else (None, None)
            self.event = ResponseEvent(
                event_id=f"ir-{timestamp_ns}",
                entered_at_ns=timestamp_ns,
                generation=generation,
                frozen_base_mw=base_value,
                frozen_base_timestamp_ns=base_ts,
            )
            self.phase = ResponsePhase.RESPONDING
        elif self.phase == ResponsePhase.RECOVERING:
            if timestamp_ns < self._recover_until_ns:
                return 0.0, None
            self.phase = ResponsePhase.IDLE
            if outside:
                return self.update(
                    rocof_hz_per_second, timestamp_ns,
                    generation=generation, frozen_base=frozen_base,
                )
            return 0.0, None
        if outside:
            assert self.event is not None
            change = -self.parameters.gain_mw_second_per_hz * rocof_hz_per_second
            delta = max(
                -self.parameters.max_decrease_mw,
                min(self.parameters.max_increase_mw, change),
            )
            return delta, self.event.event_id
        self.phase = ResponsePhase.RECOVERING
        self.event = None
        self._recover_until_ns = timestamp_ns + int(self.recovery_s * 1_000_000_000)
        return 0.0, None


def _measurement_value(
    record: dict[str, Any] | None,
    key: tuple[str, str],
    now_ns: int,
    max_age_ns: int,
) -> tuple[float, int]:
    """频率测点质量、数值与时效校验；任何一项失败都视为测点失效。"""
    if record is None:
        raise ValueError(f"{key}: missing frequency measurement")
    if str(record.get("quality", "")).lower() != "good":
        raise ValueError(f"{key}: bad frequency measurement quality")
    number = record.get("value")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise ValueError(f"{key}: numeric frequency measurement required")  # noqa: TRY004 - 与快照校验保持同一异常语义
    reading = float(number)
    if not math.isfinite(reading):
        raise ValueError(f"{key}: non-finite frequency measurement")
    try:
        timestamp = datetime.fromisoformat(str(record["timestamp"]))
        if timestamp.utcoffset() is None:
            raise ValueError("naive measurement timestamp")
        stamp_ns = int(timestamp.timestamp() * 1_000_000_000)
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise ValueError(f"{key}: invalid frequency timestamp") from error
    if stamp_ns > now_ns or now_ns - stamp_ns > max_age_ns:
        raise ValueError(f"{key}: stale or future frequency measurement")
    return reading, stamp_ns


@dataclass(slots=True)
class FrequencyResponseService:
    """单个规划任务的内部 PFR/IR 参考周期生成与事件管理。

    仅在对应功能的参考策略以 INTERNAL 为主来源时装配；
    同一事件的内外部双算由 ReferenceManager 单来源仲裁排除。
    """

    internal: InternalReferenceService
    source: MeasurementSource
    device_id: str
    point_id: str
    target: str
    valid_for_ns: int
    generation: int
    maximum_age_ns: int
    base_purpose: ReferencePurpose = ReferencePurpose.ACTIVE_BASE
    authorized_sources: frozenset[ReferenceSource] = frozenset({ReferenceSource.INTERNAL})
    pfr: PfrEventMachine | None = None
    ir: IrEventMachine | None = None
    rocof: RocofEstimator | None = None
    _last_published_ns: dict[ReferencePurpose, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.valid_for_ns <= 0 or self.maximum_age_ns <= 0 or self.generation < 0:
            raise ValueError("invalid frequency response timing or generation")
        if not self.device_id or not self.point_id or not self.target:
            raise ValueError("frequency measurement binding is required")
        if self.ir is not None and self.rocof is None:
            raise ValueError("IR event machine requires a RoCoF estimator")
        if self.pfr is None and self.ir is None:
            raise ValueError("frequency response requires PFR or IR")

    def reset(self) -> None:
        """任务停止、测点失效或功能退出后清空全部事件与估计状态。"""
        if self.pfr is not None:
            self.pfr.reset()
        if self.ir is not None:
            self.ir.reset()
        if self.rocof is not None:
            self.rocof.reset()
        self._last_published_ns.clear()

    def _frozen_base(self, now_ns: int) -> tuple[float, int] | None:
        """事件进入时锁定的 AGC 参考；无有效 AGC 参考则记录为空。"""
        try:
            selected = self.internal.manager.current(
                self.base_purpose,
                self.target,
                now_ns=now_ns,
                authorized_sources=self.authorized_sources,
            )
        except KeyError:
            return None
        if selected is None:
            return None
        return selected.control.value, selected.timestamp_ns

    def _publish(
        self,
        purpose: ReferencePurpose,
        function: ControlFunction,
        delta: float,
        event_id: str | None,
        timestamp_ns: int,
    ) -> None:
        previous = self._last_published_ns.get(purpose)
        if previous is not None and timestamp_ns <= previous:
            return
        if purpose == ReferencePurpose.PFR_DELTA:
            assert self.pfr is not None
            self.internal.pfr_delta(
                delta,
                target=self.target,
                timestamp_ns=timestamp_ns,
                valid_until_ns=timestamp_ns + self.valid_for_ns,
                generation=self.generation,
                event_id=event_id,
            )
        else:
            assert self.ir is not None
            self.internal.ir_delta(
                delta,
                target=self.target,
                timestamp_ns=timestamp_ns,
                valid_until_ns=timestamp_ns + self.valid_for_ns,
                generation=self.generation,
                event_id=event_id,
            )
        self._last_published_ns[purpose] = timestamp_ns

    async def update(self, now_ns: int) -> None:
        """读取频率测点并推进事件状态机；测点失效即复位并上抛。"""
        key = (self.device_id, self.point_id)
        records = await self.source.latest([key])
        try:
            frequency_hz, stamp_ns = _measurement_value(
                records.get(key), key, now_ns, self.maximum_age_ns,
            )
        except ValueError:
            self.reset()
            raise
        authority = self.internal.authority
        if self.pfr is not None:
            if not authority.can_output(ControlFunction.PFR):
                # 功能投出或闭锁：退出事件、复位算法，不发布内部贡献。
                self.pfr.reset()
            else:
                delta, event_id = self.pfr.update(
                    frequency_hz, stamp_ns,
                    generation=self.generation,
                    frozen_base=self._frozen_base(stamp_ns),
                )
                self._publish(
                    ReferencePurpose.PFR_DELTA, ControlFunction.PFR,
                    delta, event_id, stamp_ns,
                )
        if self.ir is not None:
            if not authority.can_output(ControlFunction.IR):
                self.ir.reset()
                if self.rocof is not None:
                    self.rocof.reset()
            else:
                assert self.rocof is not None
                estimate = self.rocof.update(frequency_hz, stamp_ns)
                if estimate is None:
                    # 估计窗口未填满：无有效 RoCoF，不触发事件也不发布。
                    return
                delta, event_id = self.ir.update(
                    estimate.value_hz_per_second, estimate.timestamp_ns,
                    generation=self.generation,
                    frozen_base=self._frozen_base(stamp_ns),
                )
                self._publish(
                    ReferencePurpose.IR_DELTA, ControlFunction.IR,
                    delta, event_id, stamp_ns,
                )

    def diagnostics(self) -> dict[str, Any]:
        """响应阶段与事件标识的可观测快照。"""
        result: dict[str, Any] = {}
        if self.pfr is not None:
            result["pfr"] = {
                "phase": self.pfr.phase.value,
                "event_id": self.pfr.event.event_id if self.pfr.event else None,
            }
        if self.ir is not None:
            result["ir"] = {
                "phase": self.ir.phase.value,
                "event_id": self.ir.event.event_id if self.ir.event else None,
            }
        return result
