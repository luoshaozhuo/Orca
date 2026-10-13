"""AGC 有功控制模式定义（设计 §6.1.3）：定值与限值互斥。"""
from __future__ import annotations

from enum import StrEnum

from core.domain.reference import ReferencePurpose


class AgcMode(StrEnum):
    """有功控制模式；限值模式下限值是约束而非跟踪目标。"""

    SETPOINT = "SETPOINT"
    LIMIT = "LIMIT"


# 模式必需的参考用途；缺失对应参考输入时配置即拒绝。
MODE_PURPOSE: dict[AgcMode, ReferencePurpose] = {
    AgcMode.SETPOINT: ReferencePurpose.ACTIVE_BASE,
    AgcMode.LIMIT: ReferencePurpose.ACTIVE_LIMIT,
}
