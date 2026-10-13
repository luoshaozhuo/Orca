"""功能投入状态机、闭锁、人工接管互锁与投退审计（设计图 7-1、§7.4）。"""
from __future__ import annotations

import pytest

from core.domain.control_authority import (
    AuthorityMode,
    ControlAuthority,
    ControlFunction,
    FunctionState,
)


def activate(authority: ControlAuthority, function: ControlFunction) -> None:
    authority.request_in_service(function, operator="test")
    authority.put_in_service(function, authorized=True, operator="test")


def test_activation_requires_pending_request() -> None:
    authority = ControlAuthority()
    with pytest.raises(RuntimeError, match="pending request"):
        authority.put_in_service(ControlFunction.AGC, authorized=True)
    authority.request_in_service(ControlFunction.AGC, operator="dispatcher")
    assert authority.states[ControlFunction.AGC] == FunctionState.REQUESTED
    with pytest.raises(RuntimeError, match="out-of-service"):
        authority.request_in_service(ControlFunction.AGC, operator="dispatcher")
    authority.put_in_service(ControlFunction.AGC, authorized=True, operator="dispatcher")
    assert authority.in_service(ControlFunction.AGC)


def test_active_and_reactive_functions_may_run_together() -> None:
    authority = ControlAuthority()
    activate(authority, ControlFunction.AGC)
    activate(authority, ControlFunction.AVC)
    assert authority.can_output(ControlFunction.AGC)
    assert authority.can_output(ControlFunction.AVC)


def test_manual_handover_requires_all_auto_out_and_outputs_released() -> None:
    authority = ControlAuthority()
    activate(authority, ControlFunction.PFR)
    with pytest.raises(RuntimeError, match="out of service"):
        authority.enter_manual(authorized=True, outputs_released=True)
    authority.take_out_of_service(ControlFunction.PFR)
    with pytest.raises(RuntimeError, match="released"):
        authority.enter_manual(authorized=True, outputs_released=False)
    authority.enter_manual(authorized=True, outputs_released=True)
    assert authority.mode == AuthorityMode.MANUAL
    with pytest.raises(RuntimeError, match="manual"):
        authority.request_in_service(ControlFunction.IR, operator="test")
    with pytest.raises(RuntimeError, match="manual"):
        authority.put_in_service(ControlFunction.IR, authorized=True)
    authority.exit_manual(authorized=True)
    assert not authority.can_output(ControlFunction.IR)


def test_block_prevents_output_and_clearing_does_not_reenable() -> None:
    authority = ControlAuthority()
    activate(authority, ControlFunction.AGC)
    authority.block(ControlFunction.AGC, "STALE_INPUT")
    assert authority.states[ControlFunction.AGC] == FunctionState.BLOCKED
    assert not authority.can_output(ControlFunction.AGC)
    # 未决投入申请期间条件不满足：REQUESTED 转入 BLOCKED，投入被拒绝。
    authority.request_in_service(ControlFunction.AVC, operator="test")
    authority.block(ControlFunction.AVC, "BAD_DATA")
    assert authority.states[ControlFunction.AVC] == FunctionState.BLOCKED
    with pytest.raises(RuntimeError, match="pending request"):
        authority.put_in_service(ControlFunction.AVC, authorized=True)
    authority.clear_block(ControlFunction.AGC, "STALE_INPUT")
    assert authority.states[ControlFunction.AGC] == FunctionState.OUT_OF_SERVICE
    assert not authority.can_output(ControlFunction.AGC)
    # 解除闭锁后须重新申请投入。
    activate(authority, ControlFunction.AGC)
    assert authority.can_output(ControlFunction.AGC)


def test_authorization_is_required_for_handover_and_service() -> None:
    authority = ControlAuthority()
    authority.request_in_service(ControlFunction.AGC, operator="test")
    with pytest.raises(PermissionError):
        authority.put_in_service(ControlFunction.AGC, authorized=False)
    with pytest.raises(PermissionError):
        authority.enter_manual(authorized=False, outputs_released=True)


def test_audit_records_operator_states_reason_and_time() -> None:
    authority = ControlAuthority()
    activate(authority, ControlFunction.PFR)
    authority.block(ControlFunction.PFR, "FREQUENCY_INVALID", operator="watchdog")
    authority.clear_block(ControlFunction.PFR, "FREQUENCY_INVALID", operator="operator-1")
    authority.enter_manual(authorized=True, outputs_released=True, operator="operator-1")
    authority.exit_manual(authorized=True, operator="operator-1")
    audits = authority.audits()
    actions = [record.action for record in audits]
    assert actions == [
        "request_in_service",
        "put_in_service",
        "block",
        "clear_block",
        "enter_manual",
        "exit_manual",
    ]
    activation = audits[1]
    assert activation.operator == "test"
    assert activation.function == ControlFunction.PFR
    assert activation.from_state == FunctionState.REQUESTED.value
    assert activation.to_state == FunctionState.IN_SERVICE.value
    block = audits[2]
    assert block.operator == "watchdog"
    assert block.reason == "FREQUENCY_INVALID"
    assert block.from_state == FunctionState.IN_SERVICE.value
    assert block.to_state == FunctionState.BLOCKED.value
    handover = audits[4]
    assert handover.function is None
    assert handover.from_state == AuthorityMode.AUTO.value
    assert handover.to_state == AuthorityMode.MANUAL.value
    timestamps = [record.timestamp_ns for record in audits]
    assert timestamps == sorted(timestamps)
    assert all(stamp > 0 for stamp in timestamps)
