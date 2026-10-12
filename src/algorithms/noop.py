"""首阶段安全策略：不产生设备指令。"""
from core.domain.decision import ControlDecision


class NoOpStrategy:
    def solve(self, measurements: dict[str, dict[str, float]]) -> ControlDecision:
        return ControlDecision()
