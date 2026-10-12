"""验证最小内存策略契约。"""

from algorithms.passthrough import PassthroughStrategy
from core.contracts import ControlProblem, ControlResult


def test_passthrough_strategy() -> None:
    """演示策略仅原样返回输入值。"""
    assert PassthroughStrategy().solve(ControlProblem(target_mw=2.5)) == ControlResult(target_mw=2.5)
