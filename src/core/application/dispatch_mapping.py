"""执行映射必须来自当前配置中的物理输出点。"""
from __future__ import annotations

from core.domain.config import OrcaConfig
from core.domain.control_value import ControlQuantity
from core.domain.safe_dispatch import OutputBinding


def validate_dispatch_bindings(
    config: OrcaConfig,
    bindings: tuple[OutputBinding, ...],
) -> None:
    """拒绝虚构或未登记的输出、缺失限幅或重复物理点。"""
    if not bindings:
        raise ValueError("dispatch requires explicit output bindings")
    points: set[tuple[str, str]] = set()
    semantics: set[tuple[str, ControlQuantity]] = set()
    for binding in bindings:
        resource = config.resources.get(binding.device_id)
        if resource is None or not resource.enabled:
            raise ValueError(f"{binding.device_id}: resource missing or disabled")
        match = [
            output
            for output in resource.outputs.values()
            if output.point_id == binding.point_id
        ]
        if len(match) != 1:
            raise ValueError("dispatch requires unique registered physical output")
        output = match[0]
        if output.minimum is None or output.maximum is None:
            raise ValueError("dispatch physical output limits must be configured")
        if (
            binding.minimum < output.minimum
            or binding.maximum > output.maximum
        ):
            raise ValueError("dispatch binding exceeds configured output limits")
        physical = (binding.device_id, binding.point_id)
        semantic = (binding.device_id, binding.quantity)
        if physical in points or semantic in semantics:
            raise ValueError("duplicate dispatch output binding")
        points.add(physical)
        semantics.add(semantic)
