"""可靠性测试复用集成层 Fixture（真实 Redis + Mock gRPC Server）。

非顶层 conftest 不允许 pytest_plugins，改为显式再导出 Fixture。
"""
from __future__ import annotations

from tests.integration.conftest import (  # noqa: F401
    data_publisher,
    orca_config,
    redis_client,
    redis_config,
    test_config,
)
