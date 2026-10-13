"""内部参考生成的应用入口：检查功能投退、闭锁和输入时效。"""
from __future__ import annotations

from dataclasses import dataclass

from core.application.reference_generators import (
    InertiaParameters,
    PFRParameters,
    generate_inertia_reference,
    generate_pfr_reference,
)
from core.application.reference_manager import ReferenceManager
from core.domain.control_authority import ControlAuthority, ControlFunction
from core.domain.reference import ControlReference, ReferenceSource


@dataclass(slots=True)
class InternalReferenceService:
    manager: ReferenceManager
    authority: ControlAuthority

    def _publish(self, function: ControlFunction, reference: ControlReference) -> ControlReference:
        if not self.authority.can_output(function):
            raise RuntimeError(f"{function}: control function not permitted")
        key = (reference.purpose, reference.target)
        policy = self.manager.policies.get(key)
        if policy is None or ReferenceSource.INTERNAL not in (
            policy.primary, *policy.backups
        ):
            raise ValueError(f"{key}: internal reference source is not configured")
        self.manager.publish(reference)
        return reference

    def pfr(
        self,
        frequency_hz: float,
        parameters: PFRParameters,
        *,
        target: str,
        timestamp_ns: int,
        valid_until_ns: int,
        generation: int,
    ) -> ControlReference:
        if not self.authority.can_output(ControlFunction.PFR):
            raise RuntimeError("PFR: control function not permitted")
        reference = generate_pfr_reference(
            frequency_hz, parameters, target=target,
            timestamp_ns=timestamp_ns, valid_until_ns=valid_until_ns,
            generation=generation,
        )
        return self._publish(ControlFunction.PFR, reference)

    def inertia(
        self,
        rocof_hz_per_second: float,
        parameters: InertiaParameters,
        *,
        target: str,
        timestamp_ns: int,
        valid_until_ns: int,
        generation: int,
    ) -> ControlReference:
        if not self.authority.can_output(ControlFunction.IR):
            raise RuntimeError("IR: control function not permitted")
        reference = generate_inertia_reference(
            rocof_hz_per_second, parameters, target=target,
            timestamp_ns=timestamp_ns, valid_until_ns=valid_until_ns,
            generation=generation,
        )
        return self._publish(ControlFunction.IR, reference)
