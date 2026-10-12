"""Commander 写命令抽象接口。"""
from __future__ import annotations

from typing import Protocol

from core.domain.decision import Setpoint


class CommandPort(Protocol):
    async def write_once(self, command_id: str, device_id: str, point_id: str, value: float) -> str:
        """只提交一次，返回 CONFIRMED_SUCCESS/FAILURE/UNKNOWN；不重试。"""
        ...
