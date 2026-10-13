#!/usr/bin/env bash
# 启动或复用持久化 Redis 容器；不会停止、覆盖其他容器。
#
# 认证约定：
#   - 用户名取 ORCA_REDIS_USERNAME，缺省 orca。
#   - 密码优先级：ORCA_REDIS_PASSWORD（非空） > REDIS_PASSWORD_FILE > 自动生成。
#   - 自动生成的密码写入密码文件（0600，原子写入），供后续启动复用。
#   - 密码与 ACL 明文不出现在命令行参数、日志或 YAML 中；ACL 只保存 SHA256 摘要。
#   - 已运行容器绝不重建；凭据与容器不一致时拒绝静默修改，要求显式轮换。
set -Eeuo pipefail

REDIS_NAME="${REDIS_NAME:-orca-redis}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
REDIS_BIND_IP="${REDIS_BIND_IP:-127.0.0.1}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-16379}"
REDIS_CONTAINER_PORT=6379
REDIS_USERNAME="${ORCA_REDIS_USERNAME:-orca}"
REDIS_DATA_DIR="${REDIS_DATA_DIR:-${HOME}/.local/share/orca/redis}"
REDIS_AUTH_DIR="${REDIS_AUTH_DIR:-${HOME}/.config/orca/redis}"
REDIS_PASSWORD_FILE="${REDIS_PASSWORD_FILE:-${REDIS_AUTH_DIR}/password}"
REDIS_ACL_FILE="${REDIS_AUTH_DIR}/users.acl"
REDIS_HEALTH_TIMEOUT="${REDIS_HEALTH_TIMEOUT:-30}"
MIN_PASSWORD_LEN=16
# Orca 读取（PING/MGET）与 wind-hub 写入（SET）的最小命令集；
# client|setinfo/setname 兼容 redis-py 连接握手，hello/auth/select 为协议基础命令。
REDIS_ACL_COMMANDS="${REDIS_ACL_COMMANDS:--@all +auth +hello +select +ping +get +mget +set +client|setinfo +client|setname}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "Docker daemon unavailable"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
[[ "$REDIS_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "invalid container name"
[[ "$REDIS_USERNAME" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid Redis username"
[[ "$REDIS_HOST_PORT" =~ ^[0-9]+$ ]] && ((10#$REDIS_HOST_PORT >= 1 && 10#$REDIS_HOST_PORT <= 65535)) || die "invalid host port"
[[ "$REDIS_BIND_IP" == 127.0.0.1 || "$REDIS_BIND_IP" == 0.0.0.0 ]] || die "bind IP must be 127.0.0.1 or 0.0.0.0"

umask 077
mkdir -p "$REDIS_DATA_DIR" "$REDIS_AUTH_DIR"
chmod 700 "$REDIS_AUTH_DIR"

# 原子写入：先写同目录临时文件再 mv，失败不破坏原有凭据。
atomic_write() { # $1=dest, content on stdin
    local dest="$1" tmp
    tmp="$(mktemp "${dest}.XXXXXX")"
    cat >"$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$dest"
}

validate_password() { # $1=password, $2=origin
    local pw="$1"
    [[ "$pw" != *$'\n'* && "$pw" != *$'\r'* ]] || die "password from $2 contains illegal newline"
    [[ "${#pw}" -ge "$MIN_PASSWORD_LEN" ]] || die "password from $2 must be at least $MIN_PASSWORD_LEN characters"
}

read_password_file() {
    [[ -s "$REDIS_PASSWORD_FILE" ]] || die "password file is empty: $REDIS_PASSWORD_FILE"
    # 权限过宽时安全修正为 600。
    local mode
    mode="$(stat -c '%a' "$REDIS_PASSWORD_FILE")"
    if [[ "$mode" != 600 ]]; then
        chmod 600 "$REDIS_PASSWORD_FILE"
        # 该函数经命令替换调用，提示必须走 stderr 以免混入密码。
        printf 'password file permissions tightened to 600\n' >&2
    fi
    cat "$REDIS_PASSWORD_FILE"
}

# 密码解析：环境变量（非空） > 密码文件 > 自动生成。
password=""
password_source=""
if [[ -n "${ORCA_REDIS_PASSWORD:-}" ]]; then
    password="$ORCA_REDIS_PASSWORD"
    validate_password "$password" "ORCA_REDIS_PASSWORD"
    password_source="env"
elif [[ -f "$REDIS_PASSWORD_FILE" ]]; then
    password="$(read_password_file)"
    validate_password "$password" "$REDIS_PASSWORD_FILE"
    password_source="file"
else
    command -v openssl >/dev/null 2>&1 || die "openssl is required to generate a password"
    password="$(openssl rand -hex 32)"
    validate_password "$password" "generator"
    printf '%s\n' "$password" | atomic_write "$REDIS_PASSWORD_FILE"
    password_source="generated"
    info "generated a random password at $REDIS_PASSWORD_FILE"
fi

password_hash="$(printf '%s' "$password" | sha256sum | cut -d ' ' -f1)"

# 带认证的 PING；密码通过 docker exec 环境变量传入，不出现在 Redis 进程参数中。
redis_ping() { # $1=password
    docker exec -e REDISCLI_AUTH="$1" "$REDIS_NAME" \
        redis-cli --user "$REDIS_USERNAME" --no-auth-warning ping 2>/dev/null | grep -q PONG
}

wait_ready() { # $1=password；有界等待，不无限阻塞。
    local deadline=$((SECONDS + REDIS_HEALTH_TIMEOUT))
    while ((SECONDS < deadline)); do
        redis_ping "$1" && return 0
        sleep 1
    done
    return 1
}

sync_password_file() { # 以环境变量为准，安全同步密码文件（容器未创建或已验证一致时）。
    if [[ ! -f "$REDIS_PASSWORD_FILE" ]] || [[ "$(cat "$REDIS_PASSWORD_FILE")" != "$password" ]]; then
        printf '%s\n' "$password" | atomic_write "$REDIS_PASSWORD_FILE"
        info "password file synchronized from ORCA_REDIS_PASSWORD"
    fi
}

write_acl() {
    printf 'user default off\nuser %s on #%s ~* &* %s\n' \
        "$REDIS_USERNAME" "$password_hash" "$REDIS_ACL_COMMANDS" | atomic_write "$REDIS_ACL_FILE"
}

if docker container inspect "$REDIS_NAME" >/dev/null 2>&1; then
    # 已存在的容器绝不重建；发现非本脚本管理的同名容器则拒绝接管。
    managed="$(docker inspect -f '{{ index .Config.Labels "org.orca.redis.managed" }}' "$REDIS_NAME")"
    [[ "$managed" == "true" ]] || die "container $REDIS_NAME exists but is not Orca-managed"
    mapped="$(docker inspect -f '{{ (index (index .HostConfig.PortBindings "6379/tcp") 0).HostIp }}:{{ (index (index .HostConfig.PortBindings "6379/tcp") 0).HostPort }}' "$REDIS_NAME")"
    [[ "$mapped" == "$REDIS_BIND_IP:$REDIS_HOST_PORT" ]] || die "existing container maps $mapped; requested $REDIS_BIND_IP:$REDIS_HOST_PORT"
    mounts="$(docker inspect -f '{{ range .Mounts }}{{ .Destination }}={{ .Source }};{{ end }}' "$REDIS_NAME")"
    [[ "$mounts" == *"/data=$REDIS_DATA_DIR;"* ]] || die "existing container data dir mismatch: $mounts"
    [[ "$mounts" == *"/usr/local/etc/redis/users.acl=$REDIS_ACL_FILE;"* ]] || die "existing container ACL mount mismatch: $mounts"
    docker update --restart always "$REDIS_NAME" >/dev/null
    running="$(docker inspect -f '{{.State.Running}}' "$REDIS_NAME")"
    if [[ "$running" != true ]]; then
        docker start "$REDIS_NAME" >/dev/null
    fi

    # 认证信息无法可靠验证时不得假定正确；不一致时拒绝静默修改，要求显式轮换。
    if wait_ready "$password"; then
        if [[ "$password_source" == "env" ]]; then
            # 环境变量为准且已通过容器认证，可安全同步密码文件（ACL 摘要本就一致）。
            sync_password_file
        fi
    elif [[ "$password_source" == "env" && -f "$REDIS_PASSWORD_FILE" ]] \
        && [[ "$(cat "$REDIS_PASSWORD_FILE")" != "$password" ]] \
        && redis_ping "$(cat "$REDIS_PASSWORD_FILE")"; then
        die "ORCA_REDIS_PASSWORD does not match existing container credentials (password file still works); explicit password rotation required"
    else
        die "Redis authentication failed for existing container $REDIS_NAME; explicit password rotation required"
    fi
    info "Redis container available: $REDIS_NAME ($mapped)"
    exit 0
fi

# 测试端口可绑定；实际 Docker 映射也会再次进行冲突检查。
if command -v python3 >/dev/null 2>&1; then
    REDIS_BIND_IP="$REDIS_BIND_IP" REDIS_HOST_PORT="$REDIS_HOST_PORT" python3 - <<'PY' || die "host port is already occupied"
import os
import socket

with socket.socket() as sock:
    sock.bind((os.environ["REDIS_BIND_IP"], int(os.environ["REDIS_HOST_PORT"])))
PY
fi

# 容器尚未创建：以解析出的密码为准同步密码文件及 ACL（ACL 仅存 SHA256 摘要）。
if [[ "$password_source" == "env" ]]; then
    sync_password_file
fi
write_acl

docker run -d \
    --name "$REDIS_NAME" \
    --label org.orca.redis.managed=true \
    --restart always \
    --user "$(id -u):$(id -g)" \
    --publish "$REDIS_BIND_IP:$REDIS_HOST_PORT:$REDIS_CONTAINER_PORT" \
    --mount "type=bind,src=$REDIS_DATA_DIR,dst=/data" \
    --mount "type=bind,src=$REDIS_ACL_FILE,dst=/usr/local/etc/redis/users.acl,readonly" \
    "$REDIS_IMAGE" redis-server \
    --aclfile /usr/local/etc/redis/users.acl \
    --appendonly yes \
    --dir /data >/dev/null

# 健康检查超时：仅清理本次新建的容器，不触碰数据目录与已有容器。
if ! wait_ready "$password"; then
    docker rm -f "$REDIS_NAME" >/dev/null 2>&1 || true
    die "Redis failed to become ready within ${REDIS_HEALTH_TIMEOUT}s; new container removed"
fi

info "Redis started: $REDIS_NAME at $REDIS_BIND_IP:$REDIS_HOST_PORT"
info "Redis username: $REDIS_USERNAME; password file: $REDIS_PASSWORD_FILE"
info "Data directory: $REDIS_DATA_DIR; restart policy: always"
