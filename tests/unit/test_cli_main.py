"""CLI 参数解析、本机 IPC 请求与配置校验（不触碰真实设备）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from cli.main import _request, main

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def run_cli(argv: list[str]) -> None:
    import sys

    old = sys.argv
    sys.argv = ["orca", *argv]
    try:
        main()
    finally:
        sys.argv = old


def test_validate_config(capsys: pytest.CaptureFixture[str]) -> None:
    run_cli(["validate-config", "--config", str(CONFIGS)])
    out = capsys.readouterr().out
    assert "valid:" in out
    assert "tasks=" in out


def test_stop_requires_task_or_all() -> None:
    with pytest.raises(SystemExit):
        run_cli(["stop", "--config", str(CONFIGS)])


@pytest_asyncio.fixture
async def ipc_server(tmp_path: Path):
    """本机回环 IPC 服务端：记录请求并按队列回复。"""
    received: list[dict] = []
    replies: asyncio.Queue[dict] = asyncio.Queue()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        payload = json.loads(await reader.readline())
        received.append(payload)
        reply = await replies.get()
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    socket_path = tmp_path / "orca-test.sock"
    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    yield socket_path, received, replies
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_request_roundtrip(ipc_server, capsys: pytest.CaptureFixture[str]) -> None:
    socket_path, received, replies = ipc_server
    await replies.put({"ok": True, "result": {"ready": False}})
    await _request(str(socket_path), {"action": "status"})
    assert received == [{"action": "status"}]
    assert '"ready": false' in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_request_error_raises(ipc_server) -> None:
    socket_path, _, replies = ipc_server
    await replies.put({"ok": False, "error": "boom"})
    with pytest.raises(RuntimeError, match="boom"):
        await _request(str(socket_path), {"action": "start", "task": "active"})


@pytest.mark.asyncio
async def test_cli_sends_action_payloads(
    ipc_server, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    socket_path, received, replies = ipc_server
    # 将配置的运行套接字指向测试回环地址。
    import cli.main as cli
    from infrastructure.config.yaml import load_config

    real_load = load_config

    def patched(path: str):
        config = real_load(path)
        return config.model_copy(
            update={"runtime": config.runtime.model_copy(
                update={"socket_path": str(socket_path)}
            )}
        )

    monkeypatch.setattr(cli, "load_config", patched)
    await replies.put({"ok": True, "result": {}})
    await replies.put({"ok": True, "result": {}})
    await replies.put({"ok": True, "result": {}})
    # main() 内部调用 asyncio.run：在独立线程中执行，复用本测试事件循环的 IPC 服务端。
    await asyncio.to_thread(run_cli, ["status", "--config", str(CONFIGS)])
    await asyncio.to_thread(run_cli, ["start", "active", "--config", str(CONFIGS)])
    await asyncio.to_thread(run_cli, ["stop", "--all", "--config", str(CONFIGS)])
    assert received == [
        {"action": "status"},
        {"action": "start", "task": "active"},
        {"action": "stop", "all": True},
    ]
