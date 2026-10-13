"""安全执行的不可变许可与命令结果契约。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.domain.control_value import ControlQuantity


class DispatchStatus(StrEnum):
    BLOCKED = "BLOCKED"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OutputBinding:
    """经 Commander 元数据验证的输出映射。"""

    device_id: str
    quantity: ControlQuantity
    point_id: str
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if not self.device_id or not self.point_id:
            raise ValueError("device and physical point are required")
        if self.quantity not in (
            ControlQuantity.ACTIVE_POWER,
            ControlQuantity.REACTIVE_POWER,
        ):
            raise ValueError("unsupported dispatch quantity")
        if not all(math.isfinite(value) for value in (self.minimum, self.maximum)):
            raise ValueError("output bounds must be finite")
        if self.minimum > self.maximum:
            raise ValueError("output bounds are inverted")


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    status: DispatchStatus
    reason: str
    command_ids: tuple[str, ...] = ()
    confirmed: int = 0
