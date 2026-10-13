"""AVC 三模式无功控制器的数值与状态行为测试。"""
from __future__ import annotations

import math

import pytest

from core.application.control_laws import VoltageReactiveLaw
from core.application.control_pipeline_factory import build_reactive_controller
from core.application.reactive_power_controller import (
    ReactivePowerCycle,
    power_factor_to_reactive,
)
from core.domain.avc import AvcMode
from core.domain.config import ControlPipelineConfig
from core.domain.control_value import (
    ControlQuantity,
    ControlScope,
    ControlValue,
    ControlValueKind,
    ControlValueRole,
)


def value(
    quantity: ControlQuantity,
    amount: float,
    role: ControlValueRole,
    unit: str,
) -> ControlValue:
    return ControlValue(
        quantity, ControlScope.POI, "POI", role,
        ControlValueKind.ABSOLUTE, amount, unit,
    )


def q_meas(amount: float) -> ControlValue:
    return value(ControlQuantity.REACTIVE_POWER, amount, ControlValueRole.MEASUREMENT, "Mvar")


def cycle(
    mode: AvcMode,
    reference: ControlValue,
    **kwargs: object,
) -> ReactivePowerCycle:
    return ReactivePowerCycle(
        mode=mode,
        reference=reference,
        q_meas=q_meas(1.0),
        dt_s=0.1,
        minimum=-10.0,
        maximum=10.0,
        **kwargs,  # type: ignore[arg-type]
    )


class TestReactivePowerMode:
    def test_direct_tracks_q_reference(self) -> None:
        controller = build_reactive_controller(AvcMode.REACTIVE_POWER, ControlPipelineConfig())
        sample = cycle(
            AvcMode.REACTIVE_POWER,
            value(ControlQuantity.REACTIVE_POWER, 3.0, ControlValueRole.REFERENCE, "Mvar"),
        )
        assert controller.evaluate(sample).value == pytest.approx(3.0)

    def test_candidate_clamped_to_limits(self) -> None:
        controller = build_reactive_controller(AvcMode.REACTIVE_POWER, ControlPipelineConfig())
        sample = cycle(
            AvcMode.REACTIVE_POWER,
            value(ControlQuantity.REACTIVE_POWER, 99.0, ControlValueRole.REFERENCE, "Mvar"),
        )
        assert controller.evaluate(sample).value == pytest.approx(10.0)

    def test_mode_mismatch_rejected(self) -> None:
        controller = build_reactive_controller(AvcMode.REACTIVE_POWER, ControlPipelineConfig())
        sample = cycle(
            AvcMode.POWER_FACTOR,
            value(ControlQuantity.POWER_FACTOR, 0.95, ControlValueRole.REFERENCE, "1"),
            p_meas=value(ControlQuantity.ACTIVE_POWER, 10.0, ControlValueRole.MEASUREMENT, "MW"),
        )
        with pytest.raises(ValueError, match="mode does not match"):
            controller.evaluate(sample)

    def test_wrong_reference_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="reference must be"):
            cycle(
                AvcMode.REACTIVE_POWER,
                value(ControlQuantity.VOLTAGE, 35.0, ControlValueRole.REFERENCE, "kV"),
            )


class TestPowerFactorMode:
    def test_lagging_pf_produces_positive_q(self) -> None:
        # pf=0.8 滞后，p=10 MW → q = 10*tan(acos(0.8)) = 7.5 Mvar。
        assert power_factor_to_reactive(0.8, 10.0) == pytest.approx(7.5)

    def test_leading_pf_produces_negative_q(self) -> None:
        assert power_factor_to_reactive(-0.8, 10.0) == pytest.approx(-7.5)

    def test_q_sign_follows_active_direction(self) -> None:
        assert power_factor_to_reactive(0.8, -10.0) == pytest.approx(-7.5)

    def test_unity_pf_gives_zero_q(self) -> None:
        assert power_factor_to_reactive(1.0, 10.0) == pytest.approx(0.0)

    def test_zero_pf_rejected(self) -> None:
        with pytest.raises(ValueError, match="power factor reference"):
            power_factor_to_reactive(0.0, 10.0)

    def test_near_zero_active_power_ill_conditioned(self) -> None:
        with pytest.raises(ValueError, match="ill-conditioned"):
            power_factor_to_reactive(0.8, 1e-6)

    def test_pf_mode_end_to_end(self) -> None:
        controller = build_reactive_controller(AvcMode.POWER_FACTOR, ControlPipelineConfig())
        sample = cycle(
            AvcMode.POWER_FACTOR,
            value(ControlQuantity.POWER_FACTOR, 0.8, ControlValueRole.REFERENCE, "1"),
            p_meas=value(ControlQuantity.ACTIVE_POWER, 10.0, ControlValueRole.MEASUREMENT, "MW"),
        )
        assert controller.evaluate(sample).value == pytest.approx(7.5)

    def test_pf_mode_requires_active_measurement(self) -> None:
        with pytest.raises(ValueError, match="requires an active power measurement"):
            cycle(
                AvcMode.POWER_FACTOR,
                value(ControlQuantity.POWER_FACTOR, 0.9, ControlValueRole.REFERENCE, "1"),
            )


class TestVoltageMode:
    def test_voltage_law_injects_q_on_undervoltage(self) -> None:
        law = VoltageReactiveLaw(kp=5.0, ki=0.0)
        # u 低于参考 0.5 kV，q_meas=1.0 → 1.0 + 5*0.5 = 3.5 Mvar。
        assert law.calculate(35.0, 34.5, 1.0, 0.1, -10.0, 10.0) == pytest.approx(3.5)

    def test_voltage_law_absorbs_q_on_overvoltage(self) -> None:
        law = VoltageReactiveLaw(kp=5.0, ki=0.0)
        assert law.calculate(35.0, 35.5, 1.0, 0.1, -10.0, 10.0) == pytest.approx(-1.5)

    def test_integral_accumulates_and_freezes_at_saturation(self) -> None:
        law = VoltageReactiveLaw(kp=0.0, ki=1.0)
        first = law.calculate(35.0, 34.0, 0.0, 1.0, -5.0, 5.0)
        assert first == pytest.approx(1.0)
        for _ in range(100):
            last = law.calculate(35.0, 34.0, 0.0, 1.0, -5.0, 5.0)
        assert last == pytest.approx(5.0)
        # 饱和后积分冻结：误差归零时不存在超调回退。
        released = law.calculate(35.0, 35.0, 0.0, 1.0, -5.0, 5.0)
        assert 0.0 <= released <= 5.0

    def test_reset_clears_integral(self) -> None:
        law = VoltageReactiveLaw(kp=0.0, ki=1.0)
        law.calculate(35.0, 34.0, 0.0, 1.0, -5.0, 5.0)
        law.reset()
        assert law.calculate(35.0, 34.0, 0.0, 1.0, -5.0, 5.0) == pytest.approx(1.0)

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_non_finite_input_rejected(self, bad: float) -> None:
        law = VoltageReactiveLaw(kp=1.0, ki=1.0)
        with pytest.raises(ValueError, match="finite"):
            law.calculate(bad, 34.0, 0.0, 1.0, -5.0, 5.0)

    def test_voltage_mode_end_to_end(self) -> None:
        config = ControlPipelineConfig.model_validate(
            {"law": {"algorithm": "pi", "kp": 5.0, "ki": 1.0}}
        )
        controller = build_reactive_controller(AvcMode.VOLTAGE, config)
        sample = cycle(
            AvcMode.VOLTAGE,
            value(ControlQuantity.VOLTAGE, 35.0, ControlValueRole.REFERENCE, "kV"),
            u_meas=value(ControlQuantity.VOLTAGE, 34.5, ControlValueRole.MEASUREMENT, "kV"),
        )
        result = controller.evaluate(sample)
        assert result.quantity == ControlQuantity.REACTIVE_POWER
        # q=1.0 + 5*0.5 + 1*0.1*0.5 = 3.55 Mvar。
        assert result.value == pytest.approx(3.55)

    def test_voltage_mode_requires_voltage_measurement(self) -> None:
        with pytest.raises(ValueError, match="requires a voltage measurement"):
            cycle(
                AvcMode.VOLTAGE,
                value(ControlQuantity.VOLTAGE, 35.0, ControlValueRole.REFERENCE, "kV"),
            )

    def test_voltage_mode_rejects_direct_law(self) -> None:
        with pytest.raises(ValueError, match="p or pi"):
            build_reactive_controller(AvcMode.VOLTAGE, ControlPipelineConfig())

    def test_failure_resets_voltage_law_state(self) -> None:
        config = ControlPipelineConfig.model_validate(
            {"law": {"algorithm": "pi", "kp": 5.0, "ki": 1.0}}
        )
        controller = build_reactive_controller(AvcMode.VOLTAGE, config)
        assert controller.voltage_law is not None
        good = cycle(
            AvcMode.VOLTAGE,
            value(ControlQuantity.VOLTAGE, 35.0, ControlValueRole.REFERENCE, "kV"),
            u_meas=value(ControlQuantity.VOLTAGE, 34.5, ControlValueRole.MEASUREMENT, "kV"),
        )
        controller.evaluate(good)
        assert controller.voltage_law._integral != 0.0
        # 绕过构造期校验注入非法周期，验证计算失败时算法状态被复位。
        broken = object.__new__(ReactivePowerCycle)
        for name in (
            "mode", "reference", "q_meas", "dt_s",
            "minimum", "maximum", "u_meas", "p_meas",
        ):
            object.__setattr__(broken, name, getattr(good, name))
        object.__setattr__(broken, "dt_s", -1.0)
        with pytest.raises(ValueError):
            controller.evaluate(broken)
        assert controller.voltage_law._integral == 0.0
