"""根据严格校验配置装配独立控制功能算法管线。"""
from __future__ import annotations

from core.application.control_laws import (
    DirectControlLaw,
    PIControlLaw,
    ProportionalControlLaw,
    VoltageReactiveLaw,
)
from core.application.control_pipeline import ControlPipeline
from core.application.control_signal import LowPassSignal, PassthroughSignal
from core.application.control_trajectory import PassthroughTrajectory, RampTrajectory
from core.application.reactive_power_controller import ReactivePowerController
from core.domain.avc import AvcMode
from core.domain.config import ControlPipelineConfig


def build_control_pipeline(config: ControlPipelineConfig) -> ControlPipeline:
    """装配无 I/O 的控制管线；不隐式初始化参考轨迹。"""
    if config.signal.algorithm == "low_pass":
        assert config.signal.tau_s is not None
        signal = LowPassSignal(tau_s=config.signal.tau_s)
    else:
        signal = PassthroughSignal()

    if config.trajectory.algorithm == "ramp":
        assert config.trajectory.up_per_s is not None
        assert config.trajectory.down_per_s is not None
        trajectory = RampTrajectory(
            up_per_s=config.trajectory.up_per_s,
            down_per_s=config.trajectory.down_per_s,
        )
    else:
        trajectory = PassthroughTrajectory()

    if config.law.algorithm == "pi":
        assert config.law.kp is not None
        assert config.law.ki is not None
        law = PIControlLaw(
            kp=config.law.kp,
            ki=config.law.ki,
            kaw=config.law.kaw or 0.0,
        )
    elif config.law.algorithm == "p":
        assert config.law.kp is not None
        law = ProportionalControlLaw(kp=config.law.kp)
    else:
        law = DirectControlLaw()

    return ControlPipeline(trajectory=trajectory, law=law, signal=signal)


def build_reactive_controller(
    mode: AvcMode,
    config: ControlPipelineConfig,
) -> ReactivePowerController:
    """装配 AVC 控制器；定电压模式的电压律增益取自 law 段 p/pi 参数。"""
    pipeline = build_control_pipeline(config)
    if mode != AvcMode.VOLTAGE:
        return ReactivePowerController(pipeline=pipeline, mode=mode)
    law = config.law
    if law.algorithm == "p":
        assert law.kp is not None
        voltage_law = VoltageReactiveLaw(kp=law.kp, ki=0.0)
    elif law.algorithm == "pi":
        assert law.kp is not None and law.ki is not None
        voltage_law = VoltageReactiveLaw(kp=law.kp, ki=law.ki)
    else:
        raise ValueError("VOLTAGE mode requires p or pi law parameters")
    return ReactivePowerController(
        pipeline=pipeline, mode=mode, voltage_law=voltage_law
    )
