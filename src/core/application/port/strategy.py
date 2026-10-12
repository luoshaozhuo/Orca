"""可替换控制策略契约。"""
from __future__ import annotations

from typing import Protocol

from core.domain.decision import ControlDecision


class ControlStrategy(Protocol):
    def solve(self, measurements: dict[str, dict[str, float]]) -> ControlDecision:
        """按业务变量名计算候选输出；不得包含 I/O。"""
        ...
