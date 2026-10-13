"""Orca 配置领域模型及跨引用验证。"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.coordination import (
    CoordinationAlgorithm,
    CoordinationFunction,
    DirectionResolution,
    ExclusiveSelect,
    PfrAgcMode,
    TieBreak,
)
from core.domain.reference import (
    ArbitrationMode,
    ReferencePurpose,
    ReferenceSelection,
    ReferenceSource,
)


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
    username: str | None = None
    password_env: str | None = None
    password_file: str | None = None


class CollectorConfig(ConfigModel):
    target: str = Field(min_length=1)


class CommanderConfig(ConfigModel):
    target: str = "127.0.0.1:50052"


class WatchdogConfig(ConfigModel):
    interval: float = Field(default=5.0, gt=0)
    timeout: float = Field(default=1.0, gt=0)


class WindHubConfig(ConfigModel):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    collector: CollectorConfig
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


class ReferencePolicyConfig(ConfigModel):
    purpose: ReferencePurpose
    target: str = Field(min_length=1)
    mode: ArbitrationMode = ArbitrationMode.FIXED
    primary: ReferenceSource
    backups: tuple[ReferenceSource, ...] = ()

    @model_validator(mode="after")
    def validate_selection(self) -> ReferencePolicyConfig:
        ReferenceSelection(
            purpose=self.purpose,
            target=self.target,
            mode=self.mode,
            primary=self.primary,
            backups=self.backups,
        )
        return self

    def to_selection(self) -> ReferenceSelection:
        return ReferenceSelection(
            purpose=self.purpose,
            target=self.target,
            mode=self.mode,
            primary=self.primary,
            backups=self.backups,
        )


class ControllerConfig(ConfigModel):
    type: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    enabled: bool = True
    references: tuple[ReferencePolicyConfig, ...] = ()

    @model_validator(mode="after")
    def validate_reference_keys(self) -> ControllerConfig:
        keys = [(policy.purpose, policy.target) for policy in self.references]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate controller reference policy")
        return self


class TaskConfig(ConfigModel):
    controller_id: str
    interval: float = Field(gt=0)
    enabled: bool = True
    resource_ids: list[str] = Field(min_length=1)


class CoordinationParametersConfig(ConfigModel):
    weights: dict[CoordinationFunction, float] = Field(default_factory=dict)
    pfr_agc_mode: PfrAgcMode | None = None
    ir_override: bool | None = None
    tie_break: TieBreak | None = None
    order: tuple[CoordinationFunction, ...] = ()
    select: ExclusiveSelect | None = None
    same_direction: DirectionResolution | None = None
    opposite_direction: DirectionResolution | None = None
    dominant: CoordinationFunction | None = None

    @model_validator(mode="after")
    def validate_values(self) -> CoordinationParametersConfig:
        for function, weight in self.weights.items():
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(
                    f"coordination weight for {function} must be finite and nonnegative"
                )
        if len(self.order) != len(set(self.order)):
            raise ValueError("coordination order contains duplicate functions")
        return self

    def validate_for(
        self,
        algorithm: CoordinationAlgorithm,
        *,
        functions: frozenset[CoordinationFunction],
    ) -> None:
        provided = self.model_fields_set
        allowed: dict[CoordinationAlgorithm, frozenset[str]] = {
            CoordinationAlgorithm.DEFAULT: frozenset(
                {"weights", "pfr_agc_mode", "ir_override"}
            ),
            CoordinationAlgorithm.SUM: frozenset({"weights"}),
            CoordinationAlgorithm.PRIORITY: frozenset({"tie_break", "order"}),
            CoordinationAlgorithm.EXCLUSIVE: frozenset({"select", "order"}),
            CoordinationAlgorithm.DIRECTIONAL: frozenset(
                {
                    "weights",
                    "same_direction",
                    "opposite_direction",
                    "dominant",
                }
            ),
        }
        unexpected = provided - allowed[algorithm]
        if unexpected:
            raise ValueError(
                f"{algorithm}: unsupported parameters {sorted(unexpected)}"
            )

        if set(self.weights) - functions:
            raise ValueError("coordination weights contain functions outside the rule")
        if set(self.order) - functions:
            raise ValueError("coordination order contains functions outside the rule")
        if self.dominant is not None and self.dominant not in functions:
            raise ValueError("coordination dominant function is outside the rule")

        if algorithm == CoordinationAlgorithm.DEFAULT:
            if (
                self.pfr_agc_mode is not None
                and self.pfr_agc_mode != PfrAgcMode.SAME_DIRECTION_ADD
            ):
                raise ValueError("unsupported default PFR/AGC mode")

        if algorithm == CoordinationAlgorithm.PRIORITY:
            tie_break = self.tie_break or TieBreak.REJECT
            if tie_break == TieBreak.FIXED_ORDER:
                if set(self.order) != functions:
                    raise ValueError(
                        "priority fixed_order must list all participating functions"
                    )
            elif self.order:
                raise ValueError("priority order requires tie_break=fixed_order")

        if algorithm == CoordinationAlgorithm.EXCLUSIVE:
            selection = self.select or ExclusiveSelect.HIGHEST_PRIORITY
            if selection == ExclusiveSelect.FIXED_ORDER:
                if set(self.order) != functions:
                    raise ValueError(
                        "exclusive fixed_order must list all participating functions"
                    )
            elif self.order:
                raise ValueError("exclusive order requires select=fixed_order")

        if algorithm == CoordinationAlgorithm.DIRECTIONAL:
            same = self.same_direction or DirectionResolution.SUM
            opposite = self.opposite_direction or DirectionResolution.SUM
            if same == DirectionResolution.SUPPRESS:
                raise ValueError("same_direction does not support suppress")
            if opposite == DirectionResolution.SUPPRESS and self.dominant is None:
                raise ValueError("directional suppress requires dominant")
            if opposite != DirectionResolution.SUPPRESS and self.dominant is not None:
                raise ValueError("dominant requires opposite_direction=suppress")


class CoordinationRuleConfig(ConfigModel):
    match: tuple[CoordinationFunction, ...] = Field(min_length=1)
    algorithm: CoordinationAlgorithm
    parameters: CoordinationParametersConfig = Field(
        default_factory=CoordinationParametersConfig
    )

    @model_validator(mode="after")
    def validate_rule(self) -> CoordinationRuleConfig:
        functions = frozenset(self.match)
        if len(functions) != len(self.match):
            raise ValueError("coordination rule match contains duplicates")
        self.parameters.validate_for(self.algorithm, functions=functions)
        return self


def _default_priority() -> dict[CoordinationFunction, int]:
    return {
        CoordinationFunction.IR: 1,
        CoordinationFunction.PFR: 2,
        CoordinationFunction.AGC: 3,
    }


class CoordinationPolicyConfig(ConfigModel):
    algorithm: CoordinationAlgorithm = CoordinationAlgorithm.DEFAULT
    priority: dict[CoordinationFunction, int] = Field(
        default_factory=_default_priority
    )
    parameters: CoordinationParametersConfig = Field(
        default_factory=CoordinationParametersConfig
    )
    rules: tuple[CoordinationRuleConfig, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> CoordinationPolicyConfig:
        functions = frozenset(CoordinationFunction)
        if set(self.priority) != functions:
            raise ValueError("coordination priority must define agc, pfr and ir")
        if any(value < 0 for value in self.priority.values()):
            raise ValueError("coordination priority must be nonnegative")
        self.parameters.validate_for(self.algorithm, functions=functions)

        matches = [frozenset(rule.match) for rule in self.rules]
        if len(matches) != len(set(matches)):
            raise ValueError("duplicate coordination rules.match")
        return self


class CoordinationConfig(ConfigModel):
    active_power: CoordinationPolicyConfig = Field(
        default_factory=CoordinationPolicyConfig
    )


class OrcaConfig(ConfigModel):
    site: SiteConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    wind_hub: WindHubConfig = Field(default_factory=WindHubConfig)
    resources: dict[str, ResourceConfig]
    controllers: dict[str, ControllerConfig]
    tasks: dict[str, TaskConfig]
    coordination: CoordinationConfig = Field(default_factory=CoordinationConfig)

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
