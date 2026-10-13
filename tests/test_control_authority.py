"""功能投入、闭锁与人工接管互锁。"""
from __future__ import annotations

import pytest

from core.domain.control_authority import (
    AuthorityMode,
    ControlAuthority,
    ControlFunction,
    FunctionService,
)


def test_active_and_reactive_functions_may_run_together() -> None:
    authority = ControlAuthority()
    authority.put_in_service(ControlFunction.AGC, authorized=True)
    authority.put_in_service(ControlFunction.AVC, authorized=True)
    assert authority.can_output(ControlFunction.AGC)
    assert authority.can_output(ControlFunction.AVC)


def test_manual_handover_requires_all_auto_out_and_outputs_released() -> None:
    authority = ControlAuthority()
    authority.put_in_service(ControlFunction.PFR, authorized=True)
    with pytest.raises(RuntimeError, match="out of service"):
        authority.enter_manual(authorized=True, outputs_released=True)
    authority.take_out_of_service(ControlFunction.PFR)
    with pytest.raises(RuntimeError, match="released"):
        authority.enter_manual(authorized=True, outputs_released=False)
    authority.enter_manual(authorized=True, outputs_released=True)
    assert authority.mode == AuthorityMode.MANUAL
    with pytest.raises(RuntimeError, match="manual"):
        authority.put_in_service(ControlFunction.IR, authorized=True)
    authority.exit_manual(authorized=True)
    assert not authority.can_output(ControlFunction.IR)


def test_block_prevents_output_and_clearing_does_not_reenable() -> None:
    authority = ControlAuthority()
    authority.put_in_service(ControlFunction.AGC, authorized=True)
    authority.block(ControlFunction.AGC, "STALE_INPUT")
    assert not authority.can_output(ControlFunction.AGC)
    with pytest.raises(RuntimeError, match="blocked"):
        authority.put_in_service(ControlFunction.AGC, authorized=True)
    authority.clear_block(ControlFunction.AGC, "STALE_INPUT")
    assert authority.functions[ControlFunction.AGC] == FunctionService.OUT_OF_SERVICE
    assert not authority.can_output(ControlFunction.AGC)


def test_authorization_is_required_for_handover_and_service() -> None:
    authority = ControlAuthority()
    with pytest.raises(PermissionError):
        authority.put_in_service(ControlFunction.AGC, authorized=False)
    with pytest.raises(PermissionError):
        authority.enter_manual(authorized=False, outputs_released=True)
