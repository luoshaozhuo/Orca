"""应用层状态快照、执行器安装与本机 IPC 处理。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from control.application import ControlApplication
from core.domain.config import OrcaConfig
from core.domain.control_authority import ControlFunction
from core.domain.control_value import ControlQuantity
from core.domain.safe_dispatch import OutputBinding


def config(socket_path: str | None = None) -> OrcaConfig:
    payload: dict = {
        "site": {"site_id": "test"},
        "wind_hub": {"collector": {"target": "127.0.0.1:50051"}},
        "resources": {
            "poi": {"type": "poi", "inputs": {"p": {"point_id": "p"}}},
            "wtg": {
                "type": "wind",
                "inputs": {
                    "p": {"point_id": "p"},
                    "q": {"point_id": "q"},
                },
                "outputs": {
                    "sp": {
                        "point_id": "active_power_setpoint",
                        "minimum": 0.0,
                        "maximum": 10.0,
                    },
                },
            },
        },
        "controllers": {
            "agc": {
                "type": "agc",
                "references": [
                    {"purpose": "ACTIVE_BASE", "target": "POI", "primary": "GRID"}
                ],
            },
        },
        "tasks": {
            "active": {
                "controller_id": "agc",
                "interval": 1.0,
                "resource_ids": ["wtg"],
                "planning": {
                    "target": "POI",
                    "poi_device_id": "poi",
                    "poi_active_point": "p",
                    "minimum": 0.0,
                    "maximum": 10.0,
                    "authorized_sources": ["GRID"],
                    "devices": [
                        {
                            "device_id": "wtg",
                            "technology": "wind",
                            "p_point": "p",
                            "q_point": "q",
                            "p_min": 0.0,
                            "p_max": 10.0,
                            "q_min": -5.0,
                            "q_max": 5.0,
                            "s_max": 10.0,
                            "p_auto": True,
                        }
                    ],
                },
            },
        },
    }
    if socket_path is not None:
        payload["runtime"] = {"socket_path": socket_path}
    return OrcaConfig.model_validate(payload)


def valid_binding() -> tuple[OutputBinding, ...]:
    return (
        OutputBinding(
            device_id="wtg",
            quantity=ControlQuantity.ACTIVE_POWER,
            point_id="active_power_setpoint",
            minimum=0.0,
            maximum=10.0,
        ),
    )


def test_status_snapshot_tracks_authority_and_audit() -> None:
    app = ControlApplication(config())
    status = app.status()
    assert status["dispatch_write_enabled"] is False
    assert status["planning_tasks"] == ["active"]
    assert status["frequency_response"] == {}
    assert status["authority_audit"] == []
    assert status["control_functions"]["AGC"]["service"] == "OUT_OF_SERVICE"

    authority = app.runtime.control_authority
    authority.request_in_service(ControlFunction.AGC, operator="dispatcher-1")
    authority.put_in_service(ControlFunction.AGC, authorized=True, operator="dispatcher-1")
    status = app.status()
    assert status["control_functions"]["AGC"]["service"] == "IN_SERVICE"
    assert status["control_functions"]["AGC"]["can_output"] is True
    actions = [record["action"] for record in status["authority_audit"]]
    assert actions == ["request_in_service", "put_in_service"]
    assert status["authority_audit"][0]["operator"] == "dispatcher-1"


def test_install_dispatch_bindings_authorization_and_validation() -> None:
    app = ControlApplication(config())
    with pytest.raises(PermissionError):
        app.install_dispatch_bindings(valid_binding(), authorized=False)
    with pytest.raises(ValueError, match="unique registered physical output"):
        app.install_dispatch_bindings(
            (
                OutputBinding(
                    device_id="wtg",
                    quantity=ControlQuantity.ACTIVE_POWER,
                    point_id="ghost_point",
                    minimum=0.0,
                    maximum=10.0,
                ),
            ),
            authorized=True,
        )
    app.install_dispatch_bindings(valid_binding(), authorized=True)
    assert app.runtime._dispatcher is not None
    # 写权限仍由看门狗验证流程控制，安装执行器不自动放开。
    assert app.runtime.write_enabled is False


@pytest.mark.asyncio
async def test_start_rejects_existing_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "orca.sock"
    socket_path.touch()
    app = ControlApplication(config(str(socket_path)))
    with pytest.raises(RuntimeError, match="already exists"):
        await app.start()


@pytest.mark.asyncio
async def test_ipc_handle_actions(tmp_path: Path) -> None:
    app = ControlApplication(config(str(tmp_path / "orca.sock")))
    server = await asyncio.start_unix_server(
        app._handle, path=str(tmp_path / "ipc.sock")
    )
    try:
        async def request(payload: dict) -> dict:
            reader, writer = await asyncio.open_unix_connection(
                str(tmp_path / "ipc.sock")
            )
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            response = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            return response

        status = await request({"action": "status"})
        assert status["ok"] is True
        assert status["result"]["site_id"] == "test"

        unknown = await request({"action": "explode"})
        assert unknown["ok"] is False
        assert "unknown operation" in unknown["error"]

        # 未就绪任务启动失败被转换为错误回复，不崩毁 IPC 服务。
        failed = await request({"action": "start", "task": "active"})
        assert failed["ok"] is False
        stopped = await request({"action": "stop", "all": True})
        assert stopped["ok"] is True

        healthy = await request({"action": "status"})
        assert healthy["ok"] is True
    finally:
        server.close()
        await server.wait_closed()
