"""使用 Docker Compose 启动隔离 Redis，并返回动态映射的主机端口。"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        capture_output=True, text=True, check=True, timeout=45,
    )
    return result.stdout.strip()


@pytest.fixture
def docker_redis_port():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable: real Redis integration not executed")
    project = "orca-test-" + uuid4().hex[:12]
    try:
        try:
            _docker("-p", project, "up", "-d", "--wait", "redis")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Docker Compose unavailable: {exc}")
        address = _docker("-p", project, "port", "redis", "6379")
        match = re.search(r":(\d+)\s*$", address)
        if match is None:
            raise RuntimeError(f"unable to resolve Redis host port: {address!r}")
        yield int(match.group(1))
    finally:
        if shutil.which("docker") is not None:
            try:
                _docker("-p", project, "down", "--volumes", "--remove-orphans")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
