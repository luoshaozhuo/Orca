"""场站控制功能投退、闭锁与全局手动互锁的纯领域逻辑。

状态机遵循设计图 7-1：OUT_OF_SERVICE → REQUESTED → IN_SERVICE；
IN_SERVICE/REQUESTED 可因失权或异常进入 BLOCKED；解除闭锁只回到
OUT_OF_SERVICE，不得自动恢复投入。全部状态迁移记录审计字段。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class ControlFunction(StrEnum):
    AGC = "AGC"
    AVC = "AVC"
    PFR = "PFR"
    IR = "IR"


class FunctionState(StrEnum):
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    REQUESTED = "REQUESTED"
    IN_SERVICE = "IN_SERVICE"
    BLOCKED = "BLOCKED"


class AuthorityMode(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class AuthorityAudit:
    """投退事务审计：操作者、时间、前后状态与原因。"""

    timestamp_ns: int
    operator: str
    function: ControlFunction | None
    action: str
    from_state: str
    to_state: str
    reason: str

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0 or not self.operator or not self.action:
            raise ValueError("audit requires timestamp, operator and action")


@dataclass(slots=True)
class ControlAuthority:
    """只管理准入状态与审计，不操作设备、不自行解除闭锁。"""

    states: dict[ControlFunction, FunctionState] = field(
        default_factory=lambda: {
            item: FunctionState.OUT_OF_SERVICE for item in ControlFunction
        }
    )
    blocks: dict[ControlFunction, set[str]] = field(
        default_factory=lambda: {item: set() for item in ControlFunction}
    )
    mode: AuthorityMode = AuthorityMode.AUTO
    audit_log: list[AuthorityAudit] = field(default_factory=list)

    def _record(
        self,
        operator: str,
        function: ControlFunction | None,
        action: str,
        from_state: str,
        to_state: str,
        reason: str,
    ) -> None:
        self.audit_log.append(
            AuthorityAudit(
                timestamp_ns=time.time_ns(),
                operator=operator,
                function=function,
                action=action,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
            )
        )

    def audits(self) -> tuple[AuthorityAudit, ...]:
        """审计记录的只读快照。"""
        return tuple(self.audit_log)

    def in_service(self, function: ControlFunction) -> bool:
        return self.states[function] == FunctionState.IN_SERVICE

    def can_output(self, function: ControlFunction) -> bool:
        return (
            self.mode == AuthorityMode.AUTO
            and self.in_service(function)
            and not self.blocks[function]
        )

    def request_in_service(
        self,
        function: ControlFunction,
        *,
        operator: str,
        reason: str = "",
    ) -> None:
        """申请投入：仅投出状态可发起；全局手动模式拒绝申请。"""
        if self.mode != AuthorityMode.AUTO:
            raise RuntimeError("global manual mode interlock")
        if self.states[function] != FunctionState.OUT_OF_SERVICE:
            raise RuntimeError("activation request requires out-of-service state")
        self.states[function] = FunctionState.REQUESTED
        self._record(
            operator, function, "request_in_service",
            FunctionState.OUT_OF_SERVICE.value, FunctionState.REQUESTED.value,
            reason,
        )

    def put_in_service(
        self,
        function: ControlFunction,
        *,
        authorized: bool,
        operator: str = "local",
        reason: str = "",
    ) -> None:
        """授权投入：必须存在未决申请；闭锁条件不满足则转入 BLOCKED。"""
        if not authorized:
            raise PermissionError("control function activation requires authorization")
        if self.mode != AuthorityMode.AUTO:
            raise RuntimeError("global manual mode interlock")
        if self.states[function] != FunctionState.REQUESTED:
            raise RuntimeError("control function activation requires a pending request")
        if self.blocks[function]:
            self.states[function] = FunctionState.BLOCKED
            self._record(
                operator, function, "put_in_service_rejected",
                FunctionState.REQUESTED.value, FunctionState.BLOCKED.value,
                "; ".join(sorted(self.blocks[function])),
            )
            raise RuntimeError("control function blocked")
        self.states[function] = FunctionState.IN_SERVICE
        self._record(
            operator, function, "put_in_service",
            FunctionState.REQUESTED.value, FunctionState.IN_SERVICE.value,
            reason,
        )

    def take_out_of_service(
        self,
        function: ControlFunction,
        *,
        operator: str = "local",
        reason: str = "",
    ) -> None:
        from_state = self.states[function]
        self.states[function] = FunctionState.OUT_OF_SERVICE
        self._record(
            operator, function, "take_out_of_service",
            from_state.value, FunctionState.OUT_OF_SERVICE.value, reason,
        )

    def block(
        self,
        function: ControlFunction,
        reason: str,
        *,
        operator: str = "system",
    ) -> None:
        if not reason:
            raise ValueError("block reason is required")
        from_state = self.states[function]
        self.blocks[function].add(reason)
        self.states[function] = FunctionState.BLOCKED
        self._record(
            operator, function, "block", from_state.value,
            FunctionState.BLOCKED.value, reason,
        )

    def clear_block(
        self,
        function: ControlFunction,
        reason: str,
        *,
        operator: str = "local",
    ) -> None:
        from_state = self.states[function]
        self.blocks[function].discard(reason)
        to_state = from_state
        if not self.blocks[function] and from_state == FunctionState.BLOCKED:
            # 解除闭锁不得自动重新投入，须重新申请并确认控制权与有效输入。
            to_state = FunctionState.OUT_OF_SERVICE
            self.states[function] = to_state
        self._record(
            operator, function, "clear_block", from_state.value, to_state.value, reason,
        )

    def enter_manual(
        self,
        *,
        authorized: bool,
        outputs_released: bool,
        operator: str = "local",
    ) -> None:
        if not authorized:
            raise PermissionError("global manual handover requires authorization")
        if not outputs_released:
            raise RuntimeError("automatic output ownership has not been released")
        if any(self.in_service(item) for item in ControlFunction):
            raise RuntimeError("all automatic functions must be out of service")
        from_mode = self.mode
        self.mode = AuthorityMode.MANUAL
        self._record(
            operator, None, "enter_manual", from_mode.value,
            AuthorityMode.MANUAL.value, "global manual handover",
        )

    def exit_manual(self, *, authorized: bool, operator: str = "local") -> None:
        if not authorized:
            raise PermissionError("global manual exit requires authorization")
        from_mode = self.mode
        self.mode = AuthorityMode.AUTO
        # 不自动恢复任何自动功能；各功能须分别重新申请投入。
        self._record(
            operator, None, "exit_manual", from_mode.value,
            AuthorityMode.AUTO.value, "global manual exit",
        )
