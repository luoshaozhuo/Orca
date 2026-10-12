"""Orca 控制服务配置：只描述控制资源，不复制 wind-hub 协议配置。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteConfig(ConfigModel):
    site_id: str = Field(min_length=1)
    name: str = ""


class RedisConfig(ConfigModel):
    host: str = "127.0.0.1"
    port: int = Field(default=6379, ge=1, le=65535)
    database: int = Field(default=0, ge=0)
    key_prefix: str = Field(default="windhub", min_length=1)
    password_env: str | None = None


class CommanderConfig(ConfigModel):
    target: str = "127.0.0.1:50052"


class WatchdogConfig(ConfigModel):
    interval: float = Field(default=5.0, gt=0)
    timeout: float = Field(default=1.0, gt=0)


class WindHubConfig(ConfigModel):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    commander: CommanderConfig = Field(default_factory=CommanderConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)


class RuntimeConfig(ConfigModel):
    shutdown_timeout: float = Field(default=10.0, gt=0)
    socket_path: str = "/tmp/orca-control.sock"


class VariableConfig(ConfigModel):
    point_id: str = Field(min_length=1)
    required: bool = True
    stale_timeout: float = Field(default=5.0, gt=0)


class OutputConfig(ConfigModel):
    point_id: str = Field(min_length=1)


class ResourceConfig(ConfigModel):
    type: str = Field(min_length=1)
    enabled: bool = True
    inputs: dict[str, VariableConfig] = Field(default_factory=dict)
    outputs: dict[str, OutputConfig] = Field(default_factory=dict)


class ControllerConfig(ConfigModel):
    type: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    enabled: bool = True


class TaskConfig(ConfigModel):
    controller_id: str
    interval: float = Field(gt=0)
    enabled: bool = True
    resource_ids: list[str] = Field(min_length=1)


class OrcaConfig(ConfigModel):
    site: SiteConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    wind_hub: WindHubConfig = Field(default_factory=WindHubConfig)
    resources: dict[str, ResourceConfig]
    controllers: dict[str, ControllerConfig]
    tasks: dict[str, TaskConfig]

    @model_validator(mode="after")
    def validate_references(self) -> OrcaConfig:
        for name, task in self.tasks.items():
            if task.controller_id not in self.controllers:
                raise ValueError(f"task {name}: missing controller {task.controller_id}")
            if task.enabled and not self.controllers[task.controller_id].enabled:
                raise ValueError(f"task {name}: controller disabled")
            for resource_id in task.resource_ids:
                if resource_id not in self.resources:
                    raise ValueError(f"task {name}: unknown resource {resource_id}")
                if task.enabled and not self.resources[resource_id].enabled:
                    raise ValueError(f"task {name}: resource {resource_id} disabled")
        return self


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


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_config(directory: str | Path) -> OrcaConfig:
    """从四份 YAML 文件加载并严格验证 Orca 配置。"""
    base = Path(directory)
    documents: dict[str, dict] = {}
    for name in ("system", "resources", "controllers", "tasks"):
        path = base / f"{name}.yaml"
        with path.open(encoding="utf-8") as handle:
            value = yaml.load(handle, Loader=_UniqueLoader)
        if not isinstance(value, dict):
            raise ValueError(f"{path}: root must be a mapping")
        documents[name] = value
    system = documents["system"]
    extra = set(system) - {"site", "runtime", "wind_hub"}
    if extra:
        raise ValueError(f"system.yaml: unknown keys: {sorted(extra)}")
    for name, key in (("resources", "resources"), ("controllers", "controllers"), ("tasks", "tasks")):
        extra = set(documents[name]) - {key}
        if extra:
            raise ValueError(f"{name}.yaml: unknown keys: {sorted(extra)}")
    return OrcaConfig.model_validate({
        **system,
        "resources": documents["resources"].get("resources", {}),
        "controllers": documents["controllers"].get("controllers", {}),
        "tasks": documents["tasks"].get("tasks", {}),
    })
