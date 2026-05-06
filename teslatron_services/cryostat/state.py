from __future__ import annotations

from dataclasses import asdict, dataclass, field as dataclass_field
from enum import StrEnum
from time import time
from typing import Any


class CryostatMode(StrEnum):
    IDLE = "IDLE"
    HOLDING = "HOLDING"
    RAMPING_T = "RAMPING_T"
    RAMPING_B = "RAMPING_B"
    RAMPING_T_AND_B = "RAMPING_T_AND_B"
    ERROR = "ERROR"
    ABORTED = "ABORTED"


class TemperatureControlMode(StrEnum):
    UNKNOWN = "UNKNOWN"
    FIXED_HEATER = "FIXED_HEATER"
    PID_AUTO = "PID_AUTO"
    PID_USER = "PID_USER"
    FIXED_TARGET = "FIXED_TARGET"
    RAMP = "RAMP"


class GasControlMode(StrEnum):
    UNKNOWN = "UNKNOWN"
    OFF = "OFF"
    FIXED_NEEDLE = "FIXED_NEEDLE"
    PRESSURE_CONTROL = "PRESSURE_CONTROL"


class SwitchHeaterStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    OFF = "OFF"
    ON = "ON"


class MagnetAction(StrEnum):
    UNKNOWN = "UNKNOWN"
    HOLD = "HOLD"
    TO_SET = "TO_SET"
    TO_ZERO = "TO_ZERO"
    CLAMP = "CLAMP"


@dataclass(slots=True)
class PIDState:
    mode: str = "UNKNOWN"
    p: float | None = None
    i: float | None = None
    d: float | None = None


@dataclass(slots=True)
class TemperatureLoopState:
    temperature_K: float | None = None
    target_K: float | None = None
    rate_K_per_min: float | None = None
    ramp_end_K: float | None = None
    heater_percent: float | None = None
    heater_power_W: float | None = None
    heater_voltage_V: float | None = None
    heater_mode: str = "UNKNOWN"
    loop_enabled: bool | None = None
    ramp_enabled: bool | None = None
    target_reached: bool | None = None
    mode: TemperatureControlMode = TemperatureControlMode.UNKNOWN
    pid: PIDState = dataclass_field(default_factory=PIDState)
    stable: bool = False
    ramping: bool = False


@dataclass(slots=True)
class TemperatureState:
    sample: TemperatureLoopState = dataclass_field(default_factory=TemperatureLoopState)
    vti: TemperatureLoopState = dataclass_field(default_factory=TemperatureLoopState)


@dataclass(slots=True)
class FieldState:
    B_T: float | None = None
    target_T: float | None = None
    rate_T_per_min: float | None = None
    output_current_A: float | None = None
    output_voltage_V: float | None = None
    magnet_temperature_K: float | None = None
    pt1_temperature_K: float | None = None
    pt2_temperature_K: float | None = None
    action: MagnetAction = MagnetAction.UNKNOWN
    at_setpoint: bool | None = None
    at_zero: bool | None = None
    clamped: bool | None = None
    stable: bool = False
    ramping: bool = False


@dataclass(slots=True)
class SwitchHeaterState:
    status: SwitchHeaterStatus = SwitchHeaterStatus.UNKNOWN
    target_status: SwitchHeaterStatus = SwitchHeaterStatus.UNKNOWN
    ready: bool = False
    delay_s: float | None = None
    last_changed_at: float | None = None
    elapsed_s: float | None = None


@dataclass(slots=True)
class PressureState:
    mbar: float | None = None
    target_mbar: float | None = None
    needle_valve_percent: float | None = None
    mode: GasControlMode = GasControlMode.UNKNOWN


@dataclass(slots=True)
class SafetyState:
    level: str = "ok"
    message: str | None = None
    safe_to_measure: bool = True
    safe_to_change_gate: bool = True
    safe_to_change_current: bool = True


@dataclass(slots=True)
class CryostatState:
    timestamp: float = dataclass_field(default_factory=time)
    mode: CryostatMode = CryostatMode.IDLE
    temperature: TemperatureState = dataclass_field(default_factory=TemperatureState)
    field: FieldState = dataclass_field(default_factory=FieldState)
    switch_heater: SwitchHeaterState = dataclass_field(default_factory=SwitchHeaterState)
    pressure: PressureState = dataclass_field(default_factory=PressureState)
    safety: SafetyState = dataclass_field(default_factory=SafetyState)
    backend: str = "mock"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = str(self.mode)
        data["temperature"]["sample"]["mode"] = str(self.temperature.sample.mode)
        data["temperature"]["vti"]["mode"] = str(self.temperature.vti.mode)
        data["field"]["action"] = str(self.field.action)
        data["switch_heater"]["status"] = str(self.switch_heater.status)
        data["switch_heater"]["target_status"] = str(self.switch_heater.target_status)
        data["pressure"]["mode"] = str(self.pressure.mode)
        return data
