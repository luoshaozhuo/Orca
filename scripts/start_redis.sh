#!/usr/bin/env bash
# 启动或复用持久化 Redis 容器；不会停止、覆盖其他容器。
set -Eeuo pipefail

REDIS_NAME="${REDIS_NAME:-orca-redis}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
REDIS_BIND_IP="${REDIS_BIND_IP:-127.0.0.1}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-16379}"
REDIS_CONTAINER_PORT=6379
REDIS_USERNAME="${REDIS_USERNAME:-orca}"
REDIS_DATA_DIR="${REDIS_DATA_DIR:-${HOME}/.local/share/orca/redis}"
REDIS_AUTH_DIR="${REDIS_AUTH_DIR:-${HOME}/.config/orca/redis}"
REDIS_PASSWORD_FILE="${REDIS_PASSWORD_FILE:-${REDIS_AUTH_DIR}/password}"
REDIS_ACL_FILE="${REDIS_AUTH_DIR}/users.acl"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "Docker daemon unavailable"
[[ "$REDIS_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "invalid container name"
[[ "$REDIS_USERNAME" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid Redis username"
[[ "$REDIS_HOST_PORT" =~ ^[0-9]+$ ]] && ((10#$REDIS_HOST_PORT >= 1 && 10#$REDIS_HOST_PORT <= 65535)) || die "invalid host port"
[[ "$REDIS_BIND_IP" == 127.0.0.1 || "$REDIS_BIND_IP" == 0.0.0.0 ]] || die "bind IP must be 127.0.0.1 or 0.0.0.0"

# 已存在的容器绝不重建；发现非本脚本管理的同名容器则拒绝接管。
if docker container inspect "$REDIS_NAME" >/dev/null 2>&1; then
    managed="$(docker inspect -f '{{ index .Config.Labels "org.orca.redis.managed" }}' "$REDIS_NAME")"
    [[ "$managed" == "true" ]] || die "container $REDIS_NAME exists but is not Orca-managed"
    mapped="$(docker inspect -f '{{ (index (index .HostConfig.PortBindings "6379/tcp") 0).HostIp }}:{{ (index (index .HostConfig.PortBindings "6379/tcp") 0).HostPort }}' "$REDIS_NAME")"
    [[ "$mapped" == "$REDIS_BIND_IP:$REDIS_HOST_PORT" ]] || die "existing container maps $mapped; requested $REDIS_BIND_IP:$REDIS_HOST_PORT"
    docker update --restart always "$REDIS_NAME" >/dev/null
    running="$(docker inspect -f '{{.State.Running}}' "$REDIS_NAME")"
    if [[ "$running" != true ]]; then
        docker start "$REDIS_NAME" >/dev/null
    fi
    echo "Redis container available: $REDIS_NAME ($mapped)"
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

umask 077
mkdir -p "$REDIS_DATA_DIR" "$REDIS_AUTH_DIR"
chmod 700 "$REDIS_AUTH_DIR"
[[ -f "$REDIS_PASSWORD_FILE" ]] || die "create a strong password file at $REDIS_PASSWORD_FILE (chmod 600), then retry"
[[ -s "$REDIS_PASSWORD_FILE" ]] || die "password file is empty"
password="$(cat "$REDIS_PASSWORD_FILE")"
[[ "$password" != *$'\n'* && "$password" != *$'\r'* ]] || die "password cannot contain newlines"
[[ "${#password}" -ge 16 ]] || die "password must be at least 16 characters"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
password_hash="$(printf '%s' "$password" | sha256sum | cut -d ' ' -f1)"
unset password
# ACL 保存密码的 SHA256，不保存明文；关闭 default 用户。
printf 'user default off\nuser %s on #%s ~* &* +@all\n' "$REDIS_USERNAME" "$password_hash" > "$REDIS_ACL_FILE"
chmod 600 "$REDIS_ACL_FILE"

docker run -d \
    --name "$REDIS_NAME" \
    --label org.orca.redis.managed=true \
    --restart always \
    --publish "$REDIS_BIND_IP:$REDIS_HOST_PORT:$REDIS_CONTAINER_PORT" \
    --mount "type=bind,src=$REDIS_DATA_DIR,dst=/data" \
    --mount "type=bind,src=$REDIS_ACL_FILE,dst=/usr/local/etc/redis/users.acl,readonly" \
    "$REDIS_IMAGE" redis-server \
    --aclfile /usr/local/etc/redis/users.acl \
    --appendonly yes \
    --dir /data >/dev/null

echo "Redis started: $REDIS_NAME at $REDIS_BIND_IP:$REDIS_HOST_PORT"
echo "Redis username: $REDIS_USERNAME; password file: $REDIS_PASSWORD_FILE"
echo "Data directory: $REDIS_DATA_DIR; restart policy: always"
