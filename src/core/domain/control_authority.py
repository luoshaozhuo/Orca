"""场站控制功能投退、闭锁与全局手动互锁的纯领域逻辑。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ControlFunction(StrEnum):
    AGC = "AGC"
    AVC = "AVC"
    PFR = "PFR"
    IR = "IR"


class FunctionService(StrEnum):
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    IN_SERVICE = "IN_SERVICE"


class AuthorityMode(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


@dataclass(slots=True)
class ControlAuthority:
    """只管理准入状态，不操作设备、不自行解除闭锁。"""

    functions: dict[ControlFunction, FunctionService] = field(
        default_factory=lambda: {
            item: FunctionService.OUT_OF_SERVICE for item in ControlFunction
        }
    )
    blocks: dict[ControlFunction, set[str]] = field(
        default_factory=lambda: {item: set() for item in ControlFunction}
    )
    mode: AuthorityMode = AuthorityMode.AUTO

    def in_service(self, function: ControlFunction) -> bool:
        return self.functions[function] == FunctionService.IN_SERVICE

    def can_output(self, function: ControlFunction) -> bool:
        return (
            self.mode == AuthorityMode.AUTO
            and self.in_service(function)
            and not self.blocks[function]
        )

    def put_in_service(self, function: ControlFunction, *, authorized: bool) -> None:
        if not authorized:
            raise PermissionError("control function activation requires authorization")
        if self.mode != AuthorityMode.AUTO:
            raise RuntimeError("global manual mode interlock")
        if self.blocks[function]:
            raise RuntimeError("control function blocked")
        self.functions[function] = FunctionService.IN_SERVICE

    def take_out_of_service(self, function: ControlFunction) -> None:
        self.functions[function] = FunctionService.OUT_OF_SERVICE

    def block(self, function: ControlFunction, reason: str) -> None:
        if not reason:
            raise ValueError("block reason is required")
        self.blocks[function].add(reason)

    def clear_block(self, function: ControlFunction, reason: str) -> None:
        self.blocks[function].discard(reason)
        # 解除闭锁不得自动重新投入。
        self.functions[function] = FunctionService.OUT_OF_SERVICE

    def enter_manual(self, *, authorized: bool, outputs_released: bool) -> None:
        if not authorized:
            raise PermissionError("global manual handover requires authorization")
        if not outputs_released:
            raise RuntimeError("automatic output ownership has not been released")
        if any(self.in_service(item) for item in ControlFunction):
            raise RuntimeError("all automatic functions must be out of service")
        self.mode = AuthorityMode.MANUAL

    def exit_manual(self, *, authorized: bool) -> None:
        if not authorized:
            raise PermissionError("global manual exit requires authorization")
        self.mode = AuthorityMode.AUTO
        # 不自动恢复任何自动功能。
