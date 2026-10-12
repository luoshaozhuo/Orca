"""Orca 配置领域模型及跨引用验证。"""
from __future__ import annotations

import math

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
    data_type: str | None = None
    required: bool = True
    stale_timeout: float = Field(default=5.0, gt=0)


class OutputConfig(ConfigModel):
    point_id: str = Field(min_length=1)
    data_type: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> OutputConfig:
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"output {name} must be finite")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("output minimum exceeds maximum")
        return self


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


