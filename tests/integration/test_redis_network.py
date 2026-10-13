"""跨容器网络访问集成测试：真实执行 start_redis.sh，并从独立 Docker 网络的
模拟 Collector/Orca 客户端容器经宿主机发布端口访问 Redis。

所有资源（容器、网络、目录）使用随机名称并在结束时清理，
不影响生产 orca-redis 容器、端口和数据。
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "start_redis.sh"
IMAGE = "redis:7-alpine"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90, check=False)


class Deployment:
    def __init__(self, name: str, network: str, port: int, username: str, password: str) -> None:
        self.name = name
        self.network = network
        self.port = port
        self.username = username
        self.password = password

    def client_cli(self, *args: str, password: str | None = None, username: str | None = None) -> subprocess.CompletedProcess[str]:
        """从独立网络的客户端容器经宿主机发布地址访问 Redis。"""
        cmd = ["docker", "run", "--rm", "--network", self.network,
               "--add-host=host.docker.internal:host-gateway"]
        if password is not None:
            cmd += ["-e", f"REDISCLI_AUTH={password}"]
        cmd += [IMAGE, "redis-cli", "-h", "host.docker.internal", "-p", str(self.port),
                "--user", username or self.username, "--no-auth-warning", *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture
def deployment(tmp_path: Path):
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable: real network integration not executed")
    if _docker("info").returncode != 0:
        pytest.skip("Docker daemon unavailable: real network integration not executed")
    name = "orca-net-test-" + uuid4().hex[:12]
    network = name + "-net"
    password = secrets.token_hex(16)
    username = "orca"
    port = _free_port()
    env = os.environ.copy()
    env.update(
        REDIS_NAME=name,
        REDIS_BIND_IP="0.0.0.0",
        REDIS_HOST_PORT=str(port),
        REDIS_AUTH_DIR=str(tmp_path / "auth"),
        REDIS_DATA_DIR=str(tmp_path / "data"),
        REDIS_PASSWORD_FILE=str(tmp_path / "auth" / "password"),
        ORCA_REDIS_USERNAME=username,
        ORCA_REDIS_PASSWORD=password,
        REDIS_HEALTH_TIMEOUT="30",
    )
    dep = Deployment(name, network, port, username, password)
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            pytest.fail(f"start_redis.sh failed: {result.stderr}")
        if _docker("network", "create", network).returncode != 0:
            pytest.skip("unable to create Docker network")
        yield dep
    finally:
        _docker("rm", "-f", name)
        _docker("network", "rm", network)


def _wait_client_ping(dep: Deployment, attempts: int = 60) -> None:
    for _ in range(attempts):
        result = dep.client_cli("ping", password=dep.password)
        if "PONG" in result.stdout:
            return
        time.sleep(1)
    raise RuntimeError("Redis not reachable from client container")


@pytest.mark.integration
@pytest.mark.real_service
def test_collector_writes_and_orca_reads_over_network(deployment: Deployment) -> None:
    # 发布地址确实面向全部接口，而非仅回环。
    published = _docker("port", deployment.name, "6379").stdout
    assert "0.0.0.0:" in published

    _wait_client_ping(deployment)
    # 模拟 Collector：从独立容器写入 wind-hub 长度前缀键。
    key = "windhub:6:wtg001:12:active_power"
    payload = '{"value": 7.5, "quality": "good", "timestamp": "2026-10-13T00:00:00+00:00"}'
    written = deployment.client_cli("set", key, payload, password=deployment.password)
    assert "OK" in written.stdout, written.stderr
    # 模拟 Orca：从另一容器读取并校验内容。
    read = deployment.client_cli("get", key, password=deployment.password)
    assert payload in read.stdout


@pytest.mark.integration
@pytest.mark.real_service
def test_unauthorized_and_wrong_credentials_rejected_over_network(deployment: Deployment) -> None:
    _wait_client_ping(deployment)
    # 未认证：default 用户已关闭。
    anonymous = deployment.client_cli("ping", password=None)
    assert "NOAUTH" in anonymous.stdout
    # 错误密码。
    wrong_pw = deployment.client_cli("ping", password="0" * 32)
    assert "WRONGPASS" in (wrong_pw.stdout + wrong_pw.stderr)
    # 错误用户名。
    wrong_user = deployment.client_cli("ping", password=deployment.password, username="nobody")
    assert "WRONGPASS" in (wrong_user.stdout + wrong_user.stderr)


@pytest.mark.integration
@pytest.mark.real_service
def test_data_survives_restart_over_network(deployment: Deployment) -> None:
    _wait_client_ping(deployment)
    marker = "persist-" + uuid4().hex[:8]
    assert "OK" in deployment.client_cli("set", "orca:marker", marker, password=deployment.password).stdout

    assert _docker("restart", deployment.name).returncode == 0
    _wait_client_ping(deployment)

    read = deployment.client_cli("get", "orca:marker", password=deployment.password)
    assert marker in read.stdout
