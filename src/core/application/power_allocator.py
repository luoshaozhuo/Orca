"""有功/无功设备级加权裕度分配；纯计算、不执行写入。"""
from __future__ import annotations

import math

from core.domain.control_value import (
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.power_allocation import (
    DevicePowerCapability,
    PowerAllocationRequest,
    PowerAllocationResult,
)


class PowerAllocator:
    """按可用裕度和设备权重分配总功率增量。"""

    def allocate(self, request: PowerAllocationRequest) -> PowerAllocationResult:
        """计算可行设备目标；越过全场可行域则明确返回不可行。"""
        fixed = sum(
            item.current for item in request.devices if not item.controllable
        )
        available = tuple(item for item in request.devices if item.controllable)
        start = sum(item.current for item in available)
        demand = request.target.value - fixed - start
        limits = {
            item.device_id: (
                item.minimum - item.current,
                item.maximum - item.current,
            )
            for item in available
        }
        minimum = sum(bounds[0] for bounds in limits.values())
        maximum = sum(bounds[1] for bounds in limits.values())
        if not math.isfinite(demand) or not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValueError("allocation aggregate overflow")
        tolerance = 1e-9 * max(1.0, abs(request.target.value))
        if demand < minimum - tolerance or demand > maximum + tolerance:
            return PowerAllocationResult(
                setpoints=(),
                fixed_power=fixed,
                allocated_power=fixed + start,
                residual=demand,
                feasible=False,
            )

        remaining = min(max(demand, minimum), maximum)
        changes = {item.device_id: 0.0 for item in available}
        active = list(available)
        # 每轮至少饱和一个设备，或一次性消除残差。
        while active and abs(remaining) > tolerance:
            upward = remaining > 0
            margins = {
                item.device_id: max(
                    0.0,
                    (item.maximum - item.current - changes[item.device_id])
                    if upward else
                    (item.current + changes[item.device_id] - item.minimum),
                )
                for item in active
            }
            capacities = {
                item.device_id: margins[item.device_id] * item.weight
                for item in active
            }
            total = sum(capacities.values())
            if total <= 0 or not math.isfinite(total):
                break
            saturation = min(
                margins[item.device_id] / capacities[item.device_id]
                for item in active if capacities[item.device_id] > 0
            )
            rate = min(abs(remaining) / total, saturation)
            increments = {
                item.device_id: math.copysign(rate * capacities[item.device_id], remaining)
                for item in active
            }
            for item in active:
                changes[item.device_id] += increments[item.device_id]
            remaining -= sum(increments.values())
            active = [
                item for item in active
                if margins[item.device_id] - abs(increments[item.device_id]) > tolerance
            ]
            if rate == 0:
                break

        setpoints = tuple(
            ControlValue(
                quantity=request.target.quantity,
                scope=ControlScope.DEVICE,
                target=item.device_id,
                role=ControlValueRole.DEVICE_SETPOINT,
                kind=ControlValueKind.ABSOLUTE,
                value=item.current + changes[item.device_id],
                unit=request.target.unit,
            )
            for item in available
        )
        allocated = fixed + sum(item.value for item in setpoints)
        residual = request.target.value - allocated
        feasible = math.isfinite(allocated) and abs(residual) <= tolerance
        return PowerAllocationResult(
            setpoints=setpoints if feasible else (),
            fixed_power=fixed,
            allocated_power=allocated,
            residual=residual,
            feasible=feasible,
        )
