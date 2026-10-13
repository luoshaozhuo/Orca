"""有功多控制需求协调应用服务。"""
from __future__ import annotations

from dataclasses import dataclass

from core.domain.config import (
    CoordinationParametersConfig,
    CoordinationPolicyConfig,
    CoordinationRuleConfig,
)
from core.domain.control_value import (
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)
from core.domain.coordination import (
    ContributionDecision,
    ControlDemand,
    CoordinationAlgorithm,
    CoordinationContext,
    CoordinationFunction,
    CoordinationResult,
    DirectionResolution,
    ExclusiveSelect,
    TieBreak,
)


@dataclass(frozen=True, slots=True)
class _ResolvedPolicy:
    algorithm: CoordinationAlgorithm
    parameters: CoordinationParametersConfig
    matched_rule: tuple[CoordinationFunction, ...] | None


class ControlCoordinator:
    """按严格配置将多个有效有功需求合成为唯一场站目标。"""

    def __init__(self, policy: CoordinationPolicyConfig) -> None:
        self._policy = policy

    def coordinate(self, context: CoordinationContext) -> CoordinationResult:
        """执行一次确定性协调。

        Raises:
            ValueError: 配置在当前需求组合下无法形成唯一结果。
        """
        by_function = {item.function: item for item in context.demands}
        resolved = self._resolve_policy(frozenset(by_function))
        accepted = self._coordinate_values(
            by_function,
            resolved.algorithm,
            resolved.parameters,
        )
        dp_coord = sum(accepted.values())

        target = ControlValue(
            quantity=context.base.quantity,
            scope=context.base.scope,
            target=context.base.target,
            role=ControlValueRole.ALLOCATION_TARGET,
            kind=ControlValueKind.ABSOLUTE,
            value=context.base.value + dp_coord,
            unit=context.base.unit,
        )
        contributions = tuple(
            ContributionDecision(
                function=demand.function,
                requested=demand.value.value,
                accepted=accepted.get(demand.function, 0.0),
                reason=self._reason(demand, accepted),
            )
            for demand in context.demands
        )
        return CoordinationResult(
            target=target,
            dp_coord=dp_coord,
            algorithm=resolved.algorithm,
            matched_rule=resolved.matched_rule,
            contributions=contributions,
        )

    def _resolve_policy(
        self,
        functions: frozenset[CoordinationFunction],
    ) -> _ResolvedPolicy:
        for rule in self._policy.rules:
            if frozenset(rule.match) == functions:
                return self._from_rule(rule)
        return _ResolvedPolicy(
            algorithm=self._policy.algorithm,
            parameters=self._policy.parameters,
            matched_rule=None,
        )

    @staticmethod
    def _from_rule(rule: CoordinationRuleConfig) -> _ResolvedPolicy:
        return _ResolvedPolicy(
            algorithm=rule.algorithm,
            parameters=rule.parameters,
            matched_rule=tuple(sorted(rule.match, key=lambda item: item.value)),
        )

    def _coordinate_values(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        algorithm: CoordinationAlgorithm,
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        if not demands:
            return {}
        if algorithm == CoordinationAlgorithm.DEFAULT:
            return self._default(demands, parameters)
        if algorithm == CoordinationAlgorithm.SUM:
            return self._sum(demands, parameters)
        if algorithm == CoordinationAlgorithm.PRIORITY:
            return self._priority(demands, parameters)
        if algorithm == CoordinationAlgorithm.EXCLUSIVE:
            return self._exclusive(demands, parameters)
        if algorithm == CoordinationAlgorithm.DIRECTIONAL:
            return self._directional(demands, parameters)
        raise ValueError(f"unsupported coordination algorithm: {algorithm}")

    @staticmethod
    def _weight(
        parameters: CoordinationParametersConfig,
        function: CoordinationFunction,
    ) -> float:
        return parameters.weights.get(function, 1.0)

    def _sum(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        return {
            function: demand.value.value * self._weight(parameters, function)
            for function, demand in demands.items()
        }

    def _default(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        ir = demands.get(CoordinationFunction.IR)
        if ir is not None:
            ir_override = parameters.ir_override
            if ir_override is None or ir_override:
                return {
                    CoordinationFunction.IR: ir.value.value
                    * self._weight(parameters, CoordinationFunction.IR)
                }
            raise ValueError(
                "default ir_override=false requires an exact rule for this demand set"
            )

        pfr = demands.get(CoordinationFunction.PFR)
        agc = demands.get(CoordinationFunction.AGC)
        if pfr is not None:
            accepted = {
                CoordinationFunction.PFR: pfr.value.value
                * self._weight(parameters, CoordinationFunction.PFR)
            }
            if (
                agc is not None
                and pfr.value.value * agc.value.value > 0
            ):
                accepted[CoordinationFunction.AGC] = (
                    agc.value.value
                    * self._weight(parameters, CoordinationFunction.AGC)
                )
            return accepted

        if agc is not None:
            return {
                CoordinationFunction.AGC: agc.value.value
                * self._weight(parameters, CoordinationFunction.AGC)
            }
        return {}

    def _highest_priority(
        self,
        functions: frozenset[CoordinationFunction],
    ) -> tuple[CoordinationFunction, ...]:
        best = min(self._policy.priority[item] for item in functions)
        return tuple(
            item
            for item in functions
            if self._policy.priority[item] == best
        )

    def _priority(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        selected = self._highest_priority(frozenset(demands))
        if len(selected) == 1:
            function = selected[0]
            return {function: demands[function].value.value}

        tie_break = parameters.tie_break or TieBreak.REJECT
        if tie_break == TieBreak.REJECT:
            names = ", ".join(sorted(item.value for item in selected))
            raise ValueError(f"equal coordination priority: {names}")
        if tie_break == TieBreak.SUM:
            return {
                function: demands[function].value.value
                for function in selected
            }
        for function in parameters.order:
            if function in selected:
                return {function: demands[function].value.value}
        raise ValueError("priority fixed_order cannot resolve equal priority")

    def _exclusive(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        selection = parameters.select or ExclusiveSelect.HIGHEST_PRIORITY
        if selection == ExclusiveSelect.FIXED_ORDER:
            for function in parameters.order:
                if function in demands:
                    return {function: demands[function].value.value}
            raise ValueError("exclusive fixed_order found no active demand")

        selected = self._highest_priority(frozenset(demands))
        if len(selected) != 1:
            names = ", ".join(sorted(item.value for item in selected))
            raise ValueError(f"exclusive highest_priority tie: {names}")
        function = selected[0]
        return {function: demands[function].value.value}

    def _directional(
        self,
        demands: dict[CoordinationFunction, ControlDemand],
        parameters: CoordinationParametersConfig,
    ) -> dict[CoordinationFunction, float]:
        nonzero = {
            function: demand
            for function, demand in demands.items()
            if demand.value.value != 0.0
        }
        if not nonzero:
            return {}

        signs = {demand.value.value > 0 for demand in nonzero.values()}
        if len(signs) == 1:
            resolution = parameters.same_direction or DirectionResolution.SUM
        else:
            resolution = (
                parameters.opposite_direction
                or DirectionResolution.SUM
            )

        if resolution == DirectionResolution.SUM:
            return self._sum(nonzero, parameters)
        if resolution == DirectionResolution.PRIORITY:
            priority_parameters = CoordinationParametersConfig(
                tie_break=TieBreak.REJECT
            )
            return self._priority(nonzero, priority_parameters)

        dominant = parameters.dominant
        if dominant is None or dominant not in nonzero:
            raise ValueError("directional dominant demand is not active and nonzero")
        demand = nonzero[dominant]
        return {
            dominant: demand.value.value
            * self._weight(parameters, dominant)
        }

    @staticmethod
    def _reason(
        demand: ControlDemand,
        accepted: dict[CoordinationFunction, float],
    ) -> str:
        if demand.function not in accepted:
            if demand.value.value == 0.0:
                return "zero contribution"
            return "suppressed by coordination policy"
        if accepted[demand.function] == demand.value.value:
            return "accepted"
        return "accepted with configured weighting"
