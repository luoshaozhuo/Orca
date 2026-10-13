"""start_redis.sh 行为测试：通过 PATH 注入 fake docker，不触碰真实容器与数据。"""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "start_redis.sh"

# fake docker：以文件记录容器状态，按 ACL 文件中的 SHA256 摘要校验认证。
FAKE_DOCKER = r"""#!/usr/bin/env bash
set -Eeuo pipefail
state="${FAKE_DOCKER_STATE:?}"
mkdir -p "$state/containers"
cmd="${1:?}"; shift

acl_auth_ok() { # $1 acl file, $2 user, $3 password
    local hash
    hash="$(printf '%s' "$3" | sha256sum | cut -d' ' -f1)"
    grep -q "^user $2 on #$hash " "$1"
}

case "$cmd" in
info)
    [[ "${FAKE_DOCKER_INFO_OK:-1}" == "1" ]]
    ;;
container)
    [[ "${1:-}" == "inspect" && -f "$state/containers/${2:-}" ]]
    ;;
inspect)
    fmt=""; name=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -f) fmt="$2"; shift 2;;
            *) name="$1"; shift;;
        esac
    done
    file="$state/containers/$name"
    [[ -f "$file" ]] || exit 1
    source "$file"
    if [[ "$fmt" == *managed* ]]; then printf '%s\n' "${MANAGED:-}"
    elif [[ "$fmt" == *PortBindings* ]]; then printf '%s\n' "${MAPPED:-}"
    elif [[ "$fmt" == *Mounts* ]]; then printf '%s\n' "${MOUNTS:-}"
    elif [[ "$fmt" == *Running* ]]; then printf '%s\n' "${RUNNING:-}"
    fi
    ;;
update)
    ;;
start)
    name="${@: -1}"
    echo "start $name" >> "$state/invocations.log"
    sed -i 's/^RUNNING=.*/RUNNING=true/' "$state/containers/$name"
    ;;
run)
    echo "run $*" >> "$state/invocations.log"
    name=""; publish=""; aclsrc=""; datasrc=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) name="$2"; shift 2;;
            --publish) publish="$2"; shift 2;;
            --mount)
                m="$2"
                case "$m" in
                    *dst=/usr/local/etc/redis/users.acl*) aclsrc="${m#*src=}"; aclsrc="${aclsrc%%,*}";;
                    *dst=/data*) datasrc="${m#*src=}"; datasrc="${datasrc%%,*}";;
                esac
                shift 2;;
            *) shift;;
        esac
    done
    [[ -n "$name" && ! -f "$state/containers/$name" ]] || exit 1
    ip="${publish%%:*}"; rest="${publish#*:}"; port="${rest%%:*}"
    cat > "$state/containers/$name" <<EOF
MANAGED=true
MAPPED="$ip:$port"
MOUNTS="/data=$datasrc;/usr/local/etc/redis/users.acl=$aclsrc;"
RUNNING=true
ACLFILE="$aclsrc"
EOF
    ;;
rm)
    name="${@: -1}"
    echo "rm $name" >> "$state/invocations.log"
    rm -f "$state/containers/$name"
    ;;
exec)
    authpw=""
    while [[ "${1:-}" == "-e" ]]; do
        kv="$2"
        [[ "$kv" == REDISCLI_AUTH=* ]] && authpw="${kv#REDISCLI_AUTH=}"
        shift 2
    done
    name="$1"; shift
    file="$state/containers/$name"
    [[ -f "$file" ]] || exit 1
    source "$file"
    [[ "${FAKE_DOCKER_AUTH_FAIL:-0}" != "1" ]] || exit 1
    [[ "$RUNNING" == "true" ]] || exit 1
    user=""
    while [[ $# -gt 0 ]]; do
        case "$1" in --user) user="$2"; shift 2;; *) shift;; esac
    done
    acl_auth_ok "$ACLFILE" "$user" "$authpw" && echo PONG
    ;;
*)
    exit 1
    ;;
esac
"""

PASSWORD = "p" * 32
OTHER_PASSWORD = "q" * 32


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def sandbox(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    auth = tmp_path / "auth"
    env = os.environ.copy()
    env.update(
        PATH=f"{bindir}:{os.environ['PATH']}",
        FAKE_DOCKER_STATE=str(tmp_path / "dockerstate"),
        REDIS_NAME="t" + uuid.uuid4().hex[:12],
        REDIS_BIND_IP="127.0.0.1",
        REDIS_HOST_PORT=str(free_port()),
        REDIS_AUTH_DIR=str(auth),
        REDIS_DATA_DIR=str(tmp_path / "data"),
        REDIS_PASSWORD_FILE=str(auth / "password"),
        REDIS_HEALTH_TIMEOUT="3",
        HOME=str(tmp_path),
    )
    for var in ("ORCA_REDIS_PASSWORD", "ORCA_REDIS_USERNAME", "FAKE_DOCKER_INFO_OK", "FAKE_DOCKER_AUTH_FAIL"):
        env.pop(var, None)
    return env


def run_script(env: dict[str, str], **extra: str) -> subprocess.CompletedProcess[str]:
    merged = {**env, **extra}
    return subprocess.run(
        ["bash", str(SCRIPT)], env=merged, capture_output=True, text=True, timeout=90,
    )


def state_dir(env: dict[str, str]) -> Path:
    return Path(env["FAKE_DOCKER_STATE"])


def container_file(env: dict[str, str]) -> Path:
    return state_dir(env) / "containers" / env["REDIS_NAME"]


def invocations(env: dict[str, str]) -> str:
    log = state_dir(env) / "invocations.log"
    return log.read_text() if log.exists() else ""


def invoked(env: dict[str, str], verb: str) -> bool:
    return any(line.startswith(verb + " ") for line in invocations(env).splitlines())


def password_file(env: dict[str, str]) -> Path:
    return Path(env["REDIS_PASSWORD_FILE"])


def acl_file(env: dict[str, str]) -> Path:
    return Path(env["REDIS_AUTH_DIR"]) / "users.acl"


def test_create_generates_password_and_acl(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox)
    assert result.returncode == 0, result.stderr
    pw_file = password_file(sandbox)
    password = pw_file.read_text().strip()
    assert len(password) == 64
    assert (pw_file.stat().st_mode & 0o777) == 0o600
    assert (Path(sandbox["REDIS_AUTH_DIR"]).stat().st_mode & 0o777) == 0o700
    acl = acl_file(sandbox).read_text()
    digest = hashlib.sha256(password.encode()).hexdigest()
    assert "user default off" in acl
    assert f"user orca on #{digest}" in acl
    assert password not in acl
    assert "-@all" in acl and "+@all" not in acl
    assert (acl_file(sandbox).stat().st_mode & 0o777) == 0o600
    assert "Redis started" in result.stdout


def test_rerun_is_idempotent(sandbox: dict[str, str]) -> None:
    assert run_script(sandbox).returncode == 0
    password = password_file(sandbox).read_text()
    second = run_script(sandbox)
    assert second.returncode == 0, second.stderr
    assert "available" in second.stdout
    assert password_file(sandbox).read_text() == password
    assert invocations(sandbox).count("run ") == 1


def test_env_credentials_used_and_file_synced(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, ORCA_REDIS_USERNAME="custom_1", ORCA_REDIS_PASSWORD=PASSWORD)
    assert result.returncode == 0, result.stderr
    assert password_file(sandbox).read_text().strip() == PASSWORD
    assert "user custom_1 on " in acl_file(sandbox).read_text()


def test_empty_env_password_falls_back_to_generation(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, ORCA_REDIS_PASSWORD="")
    assert result.returncode == 0, result.stderr
    assert len(password_file(sandbox).read_text().strip()) == 64


def test_env_password_with_newline_rejected(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, ORCA_REDIS_PASSWORD="a" * 16 + "\n" + "b" * 16)
    assert result.returncode != 0
    assert "newline" in result.stderr


def test_short_env_password_rejected(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, ORCA_REDIS_PASSWORD="short")
    assert result.returncode != 0
    assert "at least" in result.stderr


def test_existing_password_file_reused(sandbox: dict[str, str]) -> None:
    auth = Path(sandbox["REDIS_AUTH_DIR"])
    auth.mkdir(parents=True)
    password_file(sandbox).write_text(PASSWORD + "\n")
    os.chmod(password_file(sandbox), 0o600)
    result = run_script(sandbox)
    assert result.returncode == 0, result.stderr
    digest = hashlib.sha256(PASSWORD.encode()).hexdigest()
    assert f"user orca on #{digest}" in acl_file(sandbox).read_text()


def test_empty_password_file_rejected(sandbox: dict[str, str]) -> None:
    auth = Path(sandbox["REDIS_AUTH_DIR"])
    auth.mkdir(parents=True)
    password_file(sandbox).write_text("")
    result = run_script(sandbox)
    assert result.returncode != 0
    assert "empty" in result.stderr


def test_password_file_permissions_tightened(sandbox: dict[str, str]) -> None:
    auth = Path(sandbox["REDIS_AUTH_DIR"])
    auth.mkdir(parents=True)
    password_file(sandbox).write_text(PASSWORD)
    os.chmod(password_file(sandbox), 0o644)
    result = run_script(sandbox)
    assert result.returncode == 0, result.stderr
    assert (password_file(sandbox).stat().st_mode & 0o777) == 0o600


def test_invalid_username_rejected(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, ORCA_REDIS_USERNAME="bad;name")
    assert result.returncode != 0
    assert "username" in result.stderr


def test_existing_running_container_reused(sandbox: dict[str, str]) -> None:
    assert run_script(sandbox).returncode == 0
    result = run_script(sandbox)
    assert result.returncode == 0
    assert "available" in result.stdout
    assert not invoked(sandbox, "start")


def test_stopped_container_started(sandbox: dict[str, str]) -> None:
    assert run_script(sandbox).returncode == 0
    content = container_file(sandbox).read_text().replace("RUNNING=true", "RUNNING=false")
    container_file(sandbox).write_text(content)
    result = run_script(sandbox)
    assert result.returncode == 0, result.stderr
    assert invoked(sandbox, "start")
    assert "RUNNING=true" in container_file(sandbox).read_text()


def test_foreign_container_rejected(sandbox: dict[str, str]) -> None:
    containers = state_dir(sandbox) / "containers"
    containers.mkdir(parents=True)
    container_file(sandbox).write_text(
        "MANAGED=false\nMAPPED=127.0.0.1:%s\nMOUNTS=\nRUNNING=true\nACLFILE=\n"
        % sandbox["REDIS_HOST_PORT"]
    )
    result = run_script(sandbox)
    assert result.returncode != 0
    assert "not Orca-managed" in result.stderr


def test_occupied_host_port_rejected(sandbox: dict[str, str]) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", int(sandbox["REDIS_HOST_PORT"])))
        result = run_script(sandbox)
    assert result.returncode != 0
    assert "occupied" in result.stderr


def test_env_password_mismatch_requires_rotation(sandbox: dict[str, str]) -> None:
    assert run_script(sandbox).returncode == 0
    original = password_file(sandbox).read_text()
    result = run_script(sandbox, ORCA_REDIS_PASSWORD=OTHER_PASSWORD)
    assert result.returncode != 0
    assert "rotation" in result.stderr
    # 不静默修改认证：密码文件与容器均保持不变。
    assert password_file(sandbox).read_text() == original
    assert container_file(sandbox).exists()


def test_container_auth_failure_reported(sandbox: dict[str, str]) -> None:
    assert run_script(sandbox).returncode == 0
    password_file(sandbox).write_text(OTHER_PASSWORD + "\n")
    os.chmod(password_file(sandbox), 0o600)
    result = run_script(sandbox)
    assert result.returncode != 0
    assert "authentication failed" in result.stderr


def test_docker_daemon_unavailable(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, FAKE_DOCKER_INFO_OK="0")
    assert result.returncode != 0
    assert "Docker daemon unavailable" in result.stderr


def test_startup_timeout_removes_new_container(sandbox: dict[str, str]) -> None:
    result = run_script(sandbox, FAKE_DOCKER_AUTH_FAIL="1", REDIS_HEALTH_TIMEOUT="2")
    assert result.returncode != 0
    assert "removed" in result.stderr
    assert not container_file(sandbox).exists()
    assert invoked(sandbox, "rm")
