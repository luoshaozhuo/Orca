"""演示无设备连接的内存控制接口。"""

from orca.algorithms.passthrough import PassthroughStrategy
from orca.core.contracts import ControlProblem


def main() -> None:
    """运行无设备操作的示例。"""
    result = PassthroughStrategy().solve(ControlProblem(target_mw=1.0))
    print(f"Demo only: candidate target = {result.target_mw:.1f} MW")


if __name__ == "__main__":
    main()
