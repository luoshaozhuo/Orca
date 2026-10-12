"""仅供骨架联通验证的不可变控制接口。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ControlProblem:
    """演示场站目标，不代表完整控制问题。"""

    target_mw: float


@dataclass(frozen=True, slots=True)
class ControlResult:
    """候选目标，不是已通过安全校验的设备指令。"""

    target_mw: float


class ControlStrategy(Protocol):
    """无设备 I/O 的控制候选计算接口。"""

    def solve(self, problem: ControlProblem) -> ControlResult:
        """计算示例控制结果。"""
        ...
