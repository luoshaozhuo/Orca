"""控制决策：仅描述工程值，不包含协议或发送机制。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Setpoint:
    device_id: str
    variable: str
    value: float


@dataclass(frozen=True, slots=True)
class ControlDecision:
    outputs: tuple[Setpoint, ...] = ()
