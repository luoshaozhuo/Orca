"""参考值注册、来源切换与选择。

运行期参考样本按用途、控制对象和来源唯一存放；无设备写入能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.domain.reference import (
    ControlReference,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
    select_reference,
)


if TYPE_CHECKING:
    from core.domain.config import ControllerConfig


ReferenceKey = tuple[ReferencePurpose, str]


@dataclass(slots=True)
class ReferenceManager:
    policies: dict[ReferenceKey, ReferenceSelection]
    _samples: dict[
        tuple[ReferencePurpose, str, ReferenceSource],
        ControlReference,
    ] = field(default_factory=dict, init=False)
    _selected: dict[ReferenceKey, ReferenceSource] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        for key, policy in self.policies.items():
            if key != (policy.purpose, policy.target):
                raise ValueError(f"reference policy key mismatch: {key}")
            self._selected[key] = policy.primary

    @classmethod
    def from_controllers(
        cls,
        controllers: dict[str, "ControllerConfig"],
    ) -> ReferenceManager:
        """由已验证的控制器配置建立来源策略；跨控制器的键不可重复。"""
        policies: dict[ReferenceKey, ReferenceSelection] = {}
        for controller in controllers.values():
            for configured in controller.references:
                policy = configured.to_selection()
                key = (policy.purpose, policy.target)
                if key in policies:
                    raise ValueError(
                        f"reference policy {key} is already owned by another controller"
                    )
                policies[key] = policy
        return cls(policies)

    def publish(self, sample: ControlReference) -> None:
        key = (sample.purpose, sample.control.target)
        if key not in self.policies:
            raise ValueError(f"unconfigured reference: {key}")
        source_key = (*key, sample.source)
        previous = self._samples.get(source_key)
        if previous is not None and (
            sample.generation < previous.generation
            or (
                sample.generation == previous.generation
                and (
                    sample.sequence_id < previous.sequence_id
                    or (
                        sample.sequence_id == previous.sequence_id
                        and sample.timestamp_ns <= previous.timestamp_ns
                    )
                )
            )
        ):
            raise ValueError("out-of-order reference update")
        self._samples[source_key] = sample

    def select_source(
        self,
        purpose: ReferencePurpose,
        target: str,
        source: ReferenceSource,
        *,
        authorized: bool,
    ) -> None:
        if not authorized:
            raise PermissionError("reference source switch requires authorization")
        key = (purpose, target)
        policy = self.policies[key]
        if source not in (policy.primary, *policy.backups):
            raise ValueError(f"source {source} not configured for {key}")
        self._selected[key] = source

    def current(
        self,
        purpose: ReferencePurpose,
        target: str,
        *,
        now_ns: int,
        authorized_sources: frozenset[ReferenceSource],
    ) -> ControlReference | None:
        key = (purpose, target)
        policy = self.policies[key]
        samples = tuple(
            sample
            for source in (policy.primary, *policy.backups)
            if (sample := self._samples.get((*key, source))) is not None
        )
        return select_reference(
            policy,
            samples,
            now_ns=now_ns,
            authorized_sources=authorized_sources,
            confirmed_source=self._selected[key],
        )

    def clear(self, purpose: ReferencePurpose, target: str) -> None:
        """手动接管或闭锁后清除缓存，恢复时必须接收新参考。"""
        key = (purpose, target)
        for source in (self.policies[key].primary, *self.policies[key].backups):
            self._samples.pop((*key, source), None)
