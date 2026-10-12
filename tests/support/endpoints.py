"""测试端点选择：模拟服务为默认值，真实服务必须显式授权。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoints:
    collector: str | None
    commander: str | None


def select_endpoints(mode: str, *, simulated_commander: str | None = None) -> Endpoints:
    if mode == "simulated":
        return Endpoints(collector=None, commander=simulated_commander)
    if mode != "real":
        raise ValueError(f"unknown service mode: {mode}")
    if os.environ.get("ORCA_TEST_ALLOW_REAL_SERVICES") != "yes":
        raise RuntimeError("real service mode requires ORCA_TEST_ALLOW_REAL_SERVICES=yes")
    collector = os.environ.get("ORCA_TEST_COLLECTOR_TARGET")
    commander = os.environ.get("ORCA_TEST_COMMANDER_TARGET")
    if not collector or not commander:
        raise RuntimeError("both real service endpoints must be specified")
    return Endpoints(collector=collector, commander=commander)
