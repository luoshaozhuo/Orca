"""同周期 P/Q 联合分配；在矩形与视在功率圆的交集内求解。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.domain.capability_snapshot import DeviceCapabilitySnapshot
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


@dataclass(frozen=True, slots=True)
class JointAllocationResult:
    active: tuple[ControlValue, ...]
    reactive: tuple[ControlValue, ...]
    p_residual: float
    q_residual: float
    generation: int
    feasible: bool


def _clip(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _project_device(
    p: float,
    q: float,
    p_min: float,
    p_max: float,
    q_min: float,
    q_max: float,
    s_max: float,
) -> tuple[float, float]:
    """投影到盒约束与 P²+Q²<=S² 的交集，避免先截断后越圆。"""
    if math.hypot(_clip(0.0, p_min, p_max), _clip(0.0, q_min, q_max)) > s_max:
        raise ValueError("empty joint device capability")
    candidate = (_clip(p, p_min, p_max), _clip(q, q_min, q_max))
    if math.hypot(*candidate) <= s_max:
        return candidate
    # KKT: x=clip(x0/(1+lambda), bounds)，lambda 由圆边界确定。
    low, high = 0.0, 1.0
    while True:
        trial = (
            _clip(p / (1.0 + high), p_min, p_max),
            _clip(q / (1.0 + high), q_min, q_max),
        )
        if math.hypot(*trial) <= s_max:
            break
        high *= 2.0
        if not math.isfinite(high):
            raise ValueError("joint capability projection failed")
    for _ in range(70):
        mid = (low + high) / 2.0
        trial = (
            _clip(p / (1.0 + mid), p_min, p_max),
            _clip(q / (1.0 + mid), q_min, q_max),
        )
        if math.hypot(*trial) > s_max:
            low = mid
        else:
            high = mid
    return (
        _clip(p / (1.0 + high), p_min, p_max),
        _clip(q / (1.0 + high), q_min, q_max),
    )


class JointPowerAllocator:
    """Dykstra 交替投影求解凸 P/Q 可行域；不隐式降额或发送指令。"""

    def allocate(
        self,
        snapshot: DeviceCapabilitySnapshot,
        *,
        p_target: float | None,
        q_target: float | None,
        now_ns: int,
        maximum_age_ns: int,
        tolerance: float = 1e-6,
        max_iterations: int = 3000,
    ) -> JointAllocationResult:
        snapshot.validate(now_ns=now_ns, maximum_age_ns=maximum_age_ns)
        if p_target is None and q_target is None:
            raise ValueError("joint allocation requires an active P or Q target")
        if not all(
            math.isfinite(v) for v in (p_target, q_target, tolerance)
            if v is not None
        ):
            raise ValueError("non-finite joint allocation input")
        if tolerance <= 0 or max_iterations <= 0:
            raise ValueError("invalid joint allocation precision")
        devices = snapshot.bounded()
        n = len(devices)
        control_p = p_target is not None
        control_q = q_target is not None
        if not n:
            raise ValueError("joint allocation requires device capabilities")

        if p_target is None:
            p_target = math.fsum(item.p_current for item in devices)
        if q_target is None:
            q_target = math.fsum(item.q_current for item in devices)
        bounds: list[tuple[float, float, float, float, float]] = []
        for device in devices:
            pl, ph = (
                (device.p_min, device.p_max)
                if device.p_auto and control_p else (device.p_current, device.p_current)
            )
            ql, qh = (
                (device.q_min, device.q_max)
                if device.q_auto and control_q else (device.q_current, device.q_current)
            )
            bounds.append((pl, ph, ql, qh, device.s_max))

        # 快速必要条件：矩形可行域与视在功率总容量。
        # 仅用于提前判不可行；通过检查仍需进行严格联合投影。
        p_lower = math.fsum(bounds_i[0] for bounds_i in bounds)
        p_upper = math.fsum(bounds_i[1] for bounds_i in bounds)
        q_lower = math.fsum(bounds_i[2] for bounds_i in bounds)
        q_upper = math.fsum(bounds_i[3] for bounds_i in bounds)
        apparent_limit = math.fsum(item.s_max for item in devices)
        if (
            p_target < p_lower - tolerance
            or p_target > p_upper + tolerance
            or q_target < q_lower - tolerance
            or q_target > q_upper + tolerance
            or math.hypot(p_target, q_target) > apparent_limit + tolerance
        ):
            return JointAllocationResult(
                active=(), reactive=(),
                p_residual=p_target - math.fsum(item.p_current for item in devices),
                q_residual=q_target - math.fsum(item.q_current for item in devices),
                generation=snapshot.generation, feasible=False,
            )

        # 全场投影要求严格守恒，投影中只有可自动调节的维度参与修正。
        movable_p = [i for i, item in enumerate(devices) if item.p_auto and control_p]
        movable_q = [i for i, item in enumerate(devices) if item.q_auto and control_q]
        p = [item.p_current for item in devices]
        q = [item.q_current for item in devices]
        correction_p = [0.0] * n
        correction_q = [0.0] * n
        device_correction_p = [0.0] * n
        device_correction_q = [0.0] * n

        for _ in range(max_iterations):
            # 凸集 A：全场 P/Q 等式；凸集 B：逐设备矩形与能力圆。
            ap = [v + c for v, c in zip(p, correction_p)]
            aq = [v + c for v, c in zip(q, correction_q)]
            p_error = p_target - sum(ap)
            q_error = q_target - sum(aq)
            if movable_p:
                for i in movable_p:
                    ap[i] += p_error / len(movable_p)
            elif abs(p_error) > tolerance:
                break
            if movable_q:
                for i in movable_q:
                    aq[i] += q_error / len(movable_q)
            elif abs(q_error) > tolerance:
                break
            correction_p = [
                previous + correction - projected
                for previous, correction, projected in zip(p, correction_p, ap)
            ]
            correction_q = [
                previous + correction - projected
                for previous, correction, projected in zip(q, correction_q, aq)
            ]

            bp = [a + c for a, c in zip(ap, device_correction_p)]
            bq = [a + c for a, c in zip(aq, device_correction_q)]
            projected = [
                _project_device(bp[i], bq[i], *bounds[i])
                for i in range(n)
            ]
            p = [pair[0] for pair in projected]
            q = [pair[1] for pair in projected]
            device_correction_p = [
                previous - projected
                for previous, projected in zip(bp, p)
            ]
            device_correction_q = [
                previous - projected
                for previous, projected in zip(bq, q)
            ]
            if (
                abs(sum(p) - p_target) <= tolerance
                and abs(sum(q) - q_target) <= tolerance
            ):
                break

        p_residual = p_target - sum(p)
        q_residual = q_target - sum(q)
        feasible = (
            abs(p_residual) <= tolerance
            and abs(q_residual) <= tolerance
            and all(
                b[0] - tolerance <= pv <= b[1] + tolerance
                and b[2] - tolerance <= qv <= b[3] + tolerance
                and math.hypot(pv, qv) <= b[4] + tolerance
                for pv, qv, b in zip(p, q, bounds)
            )
        )
        if not feasible:
            return JointAllocationResult(
                active=(), reactive=(),
                p_residual=p_residual, q_residual=q_residual,
                generation=snapshot.generation, feasible=False,
            )

        active = tuple(
            ControlValue(
                ControlQuantity.ACTIVE_POWER, ControlScope.DEVICE,
                device.device_id, ControlValueRole.DEVICE_SETPOINT,
                ControlValueKind.ABSOLUTE, p[i], "MW",
            )
            for i, device in enumerate(devices) if device.p_auto and control_p
        )
        reactive = tuple(
            ControlValue(
                ControlQuantity.REACTIVE_POWER, ControlScope.DEVICE,
                device.device_id, ControlValueRole.DEVICE_SETPOINT,
                ControlValueKind.ABSOLUTE, q[i], "Mvar",
            )
            for i, device in enumerate(devices) if device.q_auto and control_q
        )
        return JointAllocationResult(
            active=active, reactive=reactive,
            p_residual=p_residual, q_residual=q_residual,
            generation=snapshot.generation, feasible=True,
        )
