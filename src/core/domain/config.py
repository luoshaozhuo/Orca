"""Orca 配置领域模型及跨引用验证。"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.agc import AgcMode
from core.domain.avc import MODE_PURPOSE, AvcMode
from core.domain.control_value import ControlValueKind
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
    planning_generation: int = Field(default=0, ge=0)


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
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
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


class SignalStageConfig(ConfigModel):
    algorithm: str = "passthrough"
    tau_s: float | None = None

    @model_validator(mode="after")
    def validate_stage(self) -> SignalStageConfig:
        if self.algorithm == "passthrough":
            if self.tau_s is not None:
                raise ValueError("passthrough signal does not accept tau_s")
        elif self.algorithm == "low_pass":
            if self.tau_s is None or not math.isfinite(self.tau_s) or self.tau_s <= 0:
                raise ValueError("low_pass requires positive finite tau_s")
        else:
            raise ValueError(f"unsupported signal algorithm: {self.algorithm}")
        return self


class TrajectoryStageConfig(ConfigModel):
    algorithm: str = "passthrough"
    up_per_s: float | None = None
    down_per_s: float | None = None

    @model_validator(mode="after")
    def validate_stage(self) -> TrajectoryStageConfig:
        if self.algorithm == "passthrough":
            if self.up_per_s is not None or self.down_per_s is not None:
                raise ValueError("passthrough trajectory accepts no ramp limits")
        elif self.algorithm == "ramp":
            if self.up_per_s is None or self.down_per_s is None:
                raise ValueError("ramp requires up_per_s and down_per_s")
            if any(
                not math.isfinite(value) or value < 0
                for value in (self.up_per_s, self.down_per_s)
            ):
                raise ValueError("ramp limits must be finite and nonnegative")
        else:
            raise ValueError(f"unsupported trajectory algorithm: {self.algorithm}")
        return self


class ControlLawStageConfig(ConfigModel):
    algorithm: str = "direct"
    kp: float | None = None
    ki: float | None = None
    kaw: float | None = None

    @model_validator(mode="after")
    def validate_stage(self) -> ControlLawStageConfig:
        names = {
            "direct": frozenset(),
            "active_limit": frozenset(),
            "p": frozenset({"kp"}),
            "pi": frozenset({"kp", "ki", "kaw"}),
        }
        if self.algorithm not in names:
            raise ValueError(f"unsupported law algorithm: {self.algorithm}")
        provided = {name for name in ("kp", "ki", "kaw") if getattr(self, name) is not None}
        if self.algorithm not in ("direct", "active_limit") and "kp" not in provided:
            raise ValueError("p/pi control requires kp")
        if self.algorithm == "pi" and "ki" not in provided:
            raise ValueError("pi control requires ki")
        if provided - names[self.algorithm]:
            raise ValueError("unsupported parameters for control law")
        if any(
            not math.isfinite(getattr(self, name)) or getattr(self, name) < 0
            for name in provided
        ):
            raise ValueError("control law gains must be finite and nonnegative")
        return self


class ControlPipelineConfig(ConfigModel):
    signal: SignalStageConfig = Field(default_factory=SignalStageConfig)
    trajectory: TrajectoryStageConfig = Field(default_factory=TrajectoryStageConfig)
    law: ControlLawStageConfig = Field(default_factory=ControlLawStageConfig)


class ControllerConfig(ConfigModel):
    type: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    pipeline: ControlPipelineConfig = Field(default_factory=ControlPipelineConfig)
    enabled: bool = True
    references: tuple[ReferencePolicyConfig, ...] = ()

    @model_validator(mode="after")
    def validate_reference_keys(self) -> ControllerConfig:
        keys = [(policy.purpose, policy.target) for policy in self.references]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate controller reference policy")
        return self


class PlanningStorageConfig(ConfigModel):
    soc_point: str = Field(min_length=1)
    soc_min: float = Field(ge=0, le=1)
    soc_max: float = Field(ge=0, le=1)
    energy_mwh: float = Field(gt=0)
    charge_efficiency: float = Field(gt=0, le=1)
    discharge_efficiency: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_storage(self) -> PlanningStorageConfig:
        if self.soc_min > self.soc_max:
            raise ValueError("storage SOC limits are inverted")
        for value in (
            self.energy_mwh,
            self.charge_efficiency,
            self.discharge_efficiency,
        ):
            if not math.isfinite(value):
                raise ValueError("storage energy parameters must be finite")
        return self


class PlanningDeviceConfig(ConfigModel):
    device_id: str = Field(min_length=1)
    p_point: str = Field(min_length=1)
    q_point: str = Field(min_length=1)
    technology: str
    p_min: float
    p_max: float
    q_min: float
    q_max: float
    s_max: float = Field(gt=0)
    p_auto: bool = False
    q_auto: bool = False
    p_weight: float = Field(default=1.0, gt=0)
    q_weight: float = Field(default=1.0, gt=0)
    p_ramp_up: float | None = Field(default=None, ge=0)
    p_ramp_down: float | None = Field(default=None, ge=0)
    q_ramp_up: float | None = Field(default=None, ge=0)
    q_ramp_down: float | None = Field(default=None, ge=0)
    storage: PlanningStorageConfig | None = None

    @model_validator(mode="after")
    def validate_power_bounds(self) -> PlanningDeviceConfig:
        values = (
            self.p_min, self.p_max, self.q_min, self.q_max,
            self.s_max, self.p_weight, self.q_weight,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("planning device parameters must be finite")
        if self.p_min > self.p_max or self.q_min > self.q_max:
            raise ValueError("planning device capability limits are inverted")
        if self.technology not in {"wind", "pv", "storage", "svg"}:
            raise ValueError("unknown planning device technology")
        if self.technology == "storage" and self.p_auto and self.storage is None:
            raise ValueError("storage automatic P requires a configured SOC mapping")
        if self.technology != "storage" and self.storage is not None:
            raise ValueError("storage config requires storage technology")
        for names in (("p_ramp_up", "p_ramp_down"), ("q_ramp_up", "q_ramp_down")):
            provided = [getattr(self, name) is not None for name in names]
            if any(provided) and not all(provided):
                raise ValueError("ramp rate requires both up and down parameters")
            if any(
                getattr(self, name) is not None
                and not math.isfinite(getattr(self, name))
                for name in names
            ):
                raise ValueError("device ramp rate must be finite")
        return self


class PlanningReferenceConfig(ConfigModel):
    purpose: ReferencePurpose
    source: ReferenceSource
    resource_id: str = Field(min_length=1)
    point_id: str = Field(min_length=1)
    valid_for_s: float = Field(gt=0)

    kind: ControlValueKind | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> PlanningReferenceConfig:
        # 用途白名单由所属规划上下文校验：有功与无功规划各自限定允许用途。
        if not math.isfinite(self.valid_for_s):
            raise ValueError("planning reference lifetime must be finite")
        limit_kinds = {ControlValueKind.UPPER_LIMIT, ControlValueKind.LOWER_LIMIT}
        if self.purpose == ReferencePurpose.ACTIVE_LIMIT:
            if self.kind not in limit_kinds:
                raise ValueError("ACTIVE_LIMIT reference requires an explicit limit kind")
        elif self.kind is not None:
            raise ValueError("limit kind is only valid for ACTIVE_LIMIT references")
        return self


class ActivePlanningConfig(ConfigModel):
    target: str = Field(min_length=1)
    mode: AgcMode = AgcMode.SETPOINT
    poi_device_id: str = Field(min_length=1)
    poi_active_point: str = Field(min_length=1)
    poi_available_point: str | None = None
    minimum: float
    maximum: float
    devices: tuple[PlanningDeviceConfig, ...] = Field(min_length=1)
    authorized_sources: tuple[ReferenceSource, ...] = Field(min_length=1)
    reference_inputs: tuple[PlanningReferenceConfig, ...] = ()
    active_functions: tuple[CoordinationFunction, ...] = ()
    maximum_skew_ns: int = Field(default=500_000_000, gt=0)

    @model_validator(mode="after")
    def validate_bindings(self) -> ActivePlanningConfig:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("planning target limits must be finite")
        if self.minimum > self.maximum:
            raise ValueError("planning target limits are inverted")
        names = [item.device_id for item in self.devices]
        if len(names) != len(set(names)):
            raise ValueError("duplicate planning device")
        if len(self.authorized_sources) != len(set(self.authorized_sources)):
            raise ValueError("duplicate planning reference source")
        reference_keys = [(item.purpose, item.source) for item in self.reference_inputs]
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("duplicate planning reference input")
        allowed_purposes = {
            AgcMode.SETPOINT: {
                ReferencePurpose.ACTIVE_BASE,
                ReferencePurpose.PFR_DELTA,
                ReferencePurpose.IR_DELTA,
            },
            AgcMode.LIMIT: {
                ReferencePurpose.ACTIVE_LIMIT,
                ReferencePurpose.PFR_DELTA,
                ReferencePurpose.IR_DELTA,
            },
        }[self.mode]
        if any(item.purpose not in allowed_purposes for item in self.reference_inputs):
            raise ValueError(f"{self.mode}: unsupported planning reference purpose")
        if self.mode == AgcMode.LIMIT and self.poi_available_point is None:
            raise ValueError("LIMIT mode requires an available-power measurement")
        if self.mode != AgcMode.LIMIT and self.poi_available_point is not None:
            raise ValueError("available-power measurement is only used in LIMIT mode")
        if any(item.source not in self.authorized_sources for item in self.reference_inputs):
            raise ValueError("planning reference input source is not authorized")
        if len(self.active_functions) != len(set(self.active_functions)):
            raise ValueError("duplicate planning control function")
        if set(self.active_functions) - {CoordinationFunction.PFR, CoordinationFunction.IR}:
            raise ValueError("planning active functions support PFR and IR only")
        return self


class ReactivePlanningConfig(ConfigModel):
    """AVC 无功规划：设备能力复用同任务有功规划的设备集合。"""

    target: str = Field(min_length=1)
    mode: AvcMode
    poi_device_id: str = Field(min_length=1)
    poi_reactive_point: str = Field(min_length=1)
    poi_voltage_point: str | None = None
    minimum: float
    maximum: float
    authorized_sources: tuple[ReferenceSource, ...] = Field(min_length=1)
    reference_inputs: tuple[PlanningReferenceConfig, ...] = ()
    maximum_skew_ns: int = Field(default=500_000_000, gt=0)

    @model_validator(mode="after")
    def validate_bindings(self) -> ReactivePlanningConfig:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("reactive planning limits must be finite")
        if self.minimum > self.maximum:
            raise ValueError("reactive planning limits are inverted")
        if len(self.authorized_sources) != len(set(self.authorized_sources)):
            raise ValueError("duplicate reactive planning reference source")
        allowed = {
            ReferencePurpose.REACTIVE_BASE,
            ReferencePurpose.VOLTAGE_BASE,
            ReferencePurpose.POWER_FACTOR,
        }
        keys = [(item.purpose, item.source) for item in self.reference_inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate reactive planning reference input")
        for reference in self.reference_inputs:
            if reference.purpose not in allowed:
                raise ValueError("unsupported reactive planning reference purpose")
            if reference.source not in self.authorized_sources:
                raise ValueError("reactive planning reference source is not authorized")
        required = MODE_PURPOSE[self.mode]
        if not any(item.purpose == required for item in self.reference_inputs):
            raise ValueError(f"{self.mode}: missing {required.value} reference input")
        if self.mode == AvcMode.VOLTAGE and self.poi_voltage_point is None:
            raise ValueError("VOLTAGE mode requires a POI voltage measurement")
        if self.mode != AvcMode.VOLTAGE and self.poi_voltage_point is not None:
            raise ValueError("voltage measurement is only used in VOLTAGE mode")
        return self


class TaskConfig(ConfigModel):
    controller_id: str
    planning: ActivePlanningConfig | None = None
    reactive_planning: ReactivePlanningConfig | None = None
    interval: float = Field(gt=0)
    enabled: bool = True
    resource_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_planning_pair(self) -> TaskConfig:
        # 无功规划复用有功规划的设备能力集合；纯无功任务尚无设备来源，暂不支持。
        if self.reactive_planning is not None and self.planning is None:
            raise ValueError("reactive planning requires the active planning device set")
        return self


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
        # model_dump() 往返会显式包含默认 None/空容器；它们不算用户启用参数。
        provided = {
            name for name in self.model_fields_set
            if getattr(self, name) not in (None, (), {})
        }
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

        if (
            algorithm == CoordinationAlgorithm.DEFAULT
            and self.pfr_agc_mode is not None
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
    # collector.target 无默认值，wind_hub 必须显式配置。
    wind_hub: WindHubConfig
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
            if task.planning is not None:
                controller_type = self.controllers[task.controller_id].type.lower()
                expected = "pq" if task.reactive_planning is not None else "agc"
                if controller_type != expected:
                    raise ValueError(f"task {name}: planning requires {expected} controller")
                for device in task.planning.devices:
                    if device.device_id not in task.resource_ids:
                        raise ValueError(
                            f"task {name}: planning device not in task resources"
                        )
                    resource = self.resources[device.device_id]
                    mapped = {point.point_id for point in resource.inputs.values()}
                    required = {device.p_point, device.q_point}
                    if device.storage is not None:
                        required.add(device.storage.soc_point)
                    if not required <= mapped:
                        raise ValueError(
                            f"task {name}: planning device measurement not registered"
                        )
                algorithm = self.controllers[task.controller_id].pipeline.law.algorithm
                if task.planning.mode == AgcMode.LIMIT and algorithm != "active_limit":
                    raise ValueError(
                        f"task {name}: LIMIT mode requires the active_limit control law"
                    )
                if task.planning.mode != AgcMode.LIMIT and algorithm == "active_limit":
                    raise ValueError(
                        f"task {name}: active_limit control law requires LIMIT mode"
                    )
                if task.planning.poi_device_id not in self.resources:
                    raise ValueError(
                        f"task {name}: planning POI resource is not configured"
                    )
                poi = self.resources[task.planning.poi_device_id]
                poi_required = {task.planning.poi_active_point}
                if task.planning.poi_available_point is not None:
                    poi_required.add(task.planning.poi_available_point)
                if not poi_required <= {
                    point.point_id for point in poi.inputs.values()
                }:
                    raise ValueError(f"task {name}: POI measurement not registered")
                for reference in task.planning.reference_inputs:
                    if reference.resource_id not in self.resources:
                        raise ValueError(f"task {name}: reference resource not registered")
                    resource = self.resources[reference.resource_id]
                    if reference.point_id not in {
                        point.point_id for point in resource.inputs.values()
                    }:
                        raise ValueError(f"task {name}: reference measurement not registered")
            if task.reactive_planning is not None:
                plan = task.reactive_planning
                if plan.poi_device_id not in self.resources:
                    raise ValueError(f"task {name}: reactive POI resource is not configured")
                poi = self.resources[plan.poi_device_id]
                registered = {point.point_id for point in poi.inputs.values()}
                required = {plan.poi_reactive_point}
                if plan.poi_voltage_point is not None:
                    required.add(plan.poi_voltage_point)
                if not required <= registered:
                    raise ValueError(f"task {name}: reactive POI measurement not registered")
                for reference in plan.reference_inputs:
                    if reference.resource_id not in self.resources:
                        raise ValueError(f"task {name}: reactive reference resource not registered")
                    resource = self.resources[reference.resource_id]
                    if reference.point_id not in {
                        point.point_id for point in resource.inputs.values()
                    }:
                        raise ValueError(f"task {name}: reactive reference measurement not registered")
            for resource_id in task.resource_ids:
                if resource_id not in self.resources:
                    raise ValueError(f"task {name}: unknown resource {resource_id}")
                if task.enabled and not self.resources[resource_id].enabled:
                    raise ValueError(f"task {name}: resource {resource_id} disabled")
        return self
