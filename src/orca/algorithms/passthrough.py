"""仅用于骨架验证的透传策略，禁止连接实际设备。"""

from orca.core.contracts import ControlProblem, ControlResult


class PassthroughStrategy:
    """原样返回参考值，不包含真实控制和资源分配。"""

    def solve(self, problem: ControlProblem) -> ControlResult:
        """返回候选目标。"""
        return ControlResult(target_mw=problem.target_mw)
