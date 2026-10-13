"""AVC 控制模式：同一 AVC 参考目标三选一，不与有功控制互斥。"""
from __future__ import annotations

from enum import StrEnum

from core.domain.reference import ReferencePurpose


class AvcMode(StrEnum):
    """[S4] §6.3.1～6.3.4 要求的三种电压/无功控制模式。"""

    VOLTAGE = "VOLTAGE"
    REACTIVE_POWER = "REACTIVE_POWER"
    POWER_FACTOR = "POWER_FACTOR"


# 控制模式决定参考用途；不允许凭来源名称推断数值语义。
MODE_PURPOSE: dict[AvcMode, ReferencePurpose] = {
    AvcMode.VOLTAGE: ReferencePurpose.VOLTAGE_BASE,
    AvcMode.REACTIVE_POWER: ReferencePurpose.REACTIVE_BASE,
    AvcMode.POWER_FACTOR: ReferencePurpose.POWER_FACTOR,
}
