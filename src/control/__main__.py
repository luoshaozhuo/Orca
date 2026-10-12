"""运行纯内存示例，不连接任何真实设备。"""

from algorithms.passthrough import PassthroughStrategy
from core.contracts import ControlProblem


def main() -> None:
    """执行最小控制接口示例。"""
    result = PassthroughStrategy().solve(ControlProblem(target_mw=1.0))
    print(f"Demo only: candidate target = {result.target_mw:.1f} MW")


if __name__ == "__main__":
    main()
