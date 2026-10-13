"""真实 Docker Redis 集成测试：ACL 认证、最小权限、持久化与 Orca RedisSource。

测试使用独立的 Docker Compose 项目（随机命名、tmpfs/独立卷），
不接触生产 orca-redis 容器或 ~/.local/share/orca 数据。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import redis.asyncio as redis

from core.domain.config import RedisConfig
from infrastructure.redis.source import RedisSource

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"
ACL_COMMANDS = "-@all +auth +hello +select +ping +get +mget +set +client|setinfo +client|setname"


def _docker(project: str, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "-p", project, *args],
        capture_output=True, text=True, check=True, timeout=60, env=env,
    )
    return result.stdout.strip()


def _host_port(project: str, service: str, env: dict[str, str] | None = None) -> int:
    address = _docker(project, "port", service, "6379", env=env)
    match = re.search(r":(\d+)\s*$", address)
    if match is None:
        raise RuntimeError(f"unable to resolve host port for {service}: {address!r}")
    return int(match.group(1))


async def _wait_ready(client: redis.Redis, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            if await client.ping():
                return
        except redis.RedisError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("Redis did not become ready")


@pytest.fixture
def auth_redis(tmp_path: Path):
    """带 ACL 的隔离 Redis：随机用户名/密码，最小命令权限。"""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable: real Redis integration not executed")
    username = "orca"
    password = secrets.token_hex(16)
    digest = hashlib.sha256(password.encode()).hexdigest()
    acl = tmp_path / "users.acl"
    acl.write_text(f"user default off\nuser {username} on #{digest} ~* &* {ACL_COMMANDS}\n")
    env = {**os.environ, "ORCA_TEST_ACL_FILE": str(acl)}
    project = "orca-auth-test-" + uuid4().hex[:12]
    try:
        try:
            _docker(project, "up", "-d", "redis-auth", env=env)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Docker Compose unavailable: {exc}")
        port = _host_port(project, "redis-auth", env=env)
        yield {"port": port, "username": username, "password": password}
    finally:
        if shutil.which("docker") is not None:
            try:
                _docker(project, "down", "--volumes", "--remove-orphans", env=env)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass


@pytest.fixture
def persist_redis():
    """独立卷的持久化 Redis，用于验证容器重启后数据保留。"""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable: real Redis integration not executed")
    project = "orca-persist-test-" + uuid4().hex[:12]
    try:
        try:
            _docker(project, "up", "-d", "redis-persist")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Docker Compose unavailable: {exc}")
        yield {"project": project, "port": _host_port(project, "redis-persist")}
    finally:
        if shutil.which("docker") is not None:
            try:
                _docker(project, "down", "--volumes", "--remove-orphans")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_unauthenticated_access_fails(auth_redis) -> None:
    # 先用正确凭据确认服务就绪，避免把连接失败误判为认证拒绝。
    ready = redis.Redis(
        host="127.0.0.1", port=auth_redis["port"], username=auth_redis["username"],
        password=auth_redis["password"], decode_responses=True,
    )
    await _wait_ready(ready)
    await ready.aclose()
    client = redis.Redis(host="127.0.0.1", port=auth_redis["port"], decode_responses=True)
    try:
        with pytest.raises(redis.RedisError):
            await client.ping()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_wrong_password_fails(auth_redis) -> None:
    good = redis.Redis(
        host="127.0.0.1", port=auth_redis["port"], username=auth_redis["username"],
        password=auth_redis["password"], decode_responses=True,
    )
    await _wait_ready(good)
    await good.aclose()
    bad = redis.Redis(
        host="127.0.0.1", port=auth_redis["port"], username=auth_redis["username"],
        password="0" * 32, decode_responses=True,
    )
    try:
        with pytest.raises(redis.RedisError):
            await bad.ping()
    finally:
        await bad.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_acl_minimal_permissions(auth_redis) -> None:
    client = redis.Redis(
        host="127.0.0.1", port=auth_redis["port"], username=auth_redis["username"],
        password=auth_redis["password"], decode_responses=True,
    )
    try:
        await _wait_ready(client)
        assert await client.ping()
        await client.set("windhub:1:x:1:y", "1")
        assert await client.get("windhub:1:x:1:y") == "1"
        assert await client.mget(["windhub:1:x:1:y"]) == ["1"]
        # 最小权限：危险命令被拒绝。
        with pytest.raises(redis.RedisError):
            await client.config_get("dir")
        with pytest.raises(redis.RedisError):
            await client.delete("windhub:1:x:1:y")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_redis_source_ping_set_mget(auth_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCA_TEST_SOURCE_PASSWORD", auth_redis["password"])
    config = RedisConfig(
        host="127.0.0.1", port=auth_redis["port"], key_prefix="windhub-test",
        username=auth_redis["username"], password_env="ORCA_TEST_SOURCE_PASSWORD",
    )
    source = RedisSource(config, timeout=2.0)
    writer = redis.Redis(
        host="127.0.0.1", port=auth_redis["port"], username=auth_redis["username"],
        password=auth_redis["password"], decode_responses=True,
    )
    try:
        await source.connect()
        await source.ping()
        pair = ("wtg001", "active_power")
        payload = {
            "value": 3.25, "quality": "good",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await writer.set(RedisSource.key("windhub-test", *pair), json.dumps(payload))
        records = await source.latest([pair])
        assert records[pair]["value"] == 3.25
    finally:
        await writer.aclose()
        await source.close()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.real_service
async def test_data_survives_container_restart(persist_redis) -> None:
    port = persist_redis["port"]
    client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    try:
        await _wait_ready(client)
        await client.set("orca:persist-check", "alive")
        await client.save()
    finally:
        await client.aclose()

    _docker(persist_redis["project"], "restart", "redis-persist")
    # 临时主机端口在重启后可能重新分配。
    port = _host_port(persist_redis["project"], "redis-persist")

    reopened = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    try:
        await _wait_ready(reopened)
        assert await reopened.get("orca:persist-check") == "alive"
    finally:
        await reopened.aclose()
