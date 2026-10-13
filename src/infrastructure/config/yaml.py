"""YAML 配置适配器：仅负责载入及转换。"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.domain.config import OrcaConfig


class _UniqueLoader(yaml.SafeLoader):
    """拒绝同一 YAML 映射中的重复键。"""


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _mapping,
)


def load_config(directory: str | Path) -> OrcaConfig:
    """从五份 YAML 文件加载并严格验证 Orca 配置。"""
    base = Path(directory)
    documents: dict[str, dict] = {}
    for name in (
        "system",
        "resources",
        "controllers",
        "tasks",
        "coordination",
    ):
        path = base / f"{name}.yaml"
        with path.open(encoding="utf-8") as handle:
            value = yaml.load(handle, Loader=_UniqueLoader)
        if not isinstance(value, dict):
            raise ValueError(f"{path}: root must be a mapping")  # noqa: TRY004 - config validation contract
        documents[name] = value

    system = documents["system"]
    extra = set(system) - {"site", "runtime", "wind_hub"}
    if extra:
        raise ValueError(f"system.yaml: unknown keys: {sorted(extra)}")

    roots = (
        ("resources", "resources"),
        ("controllers", "controllers"),
        ("tasks", "tasks"),
        ("coordination", "coordination"),
    )
    for name, key in roots:
        extra = set(documents[name]) - {key}
        if extra:
            raise ValueError(f"{name}.yaml: unknown keys: {sorted(extra)}")

    return OrcaConfig.model_validate(
        {
            **system,
            "resources": documents["resources"].get("resources", {}),
            "controllers": documents["controllers"].get("controllers", {}),
            "tasks": documents["tasks"].get("tasks", {}),
            "coordination": documents["coordination"].get("coordination", {}),
        }
    )
