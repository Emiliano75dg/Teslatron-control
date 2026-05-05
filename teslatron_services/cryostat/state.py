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
    stable: bool = False
    ramping: bool = False


@dataclass(slots=True)
class PressureState:
    mbar: float | None = None
    needle_valve_percent: float | None = None


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
    pressure: PressureState = dataclass_field(default_factory=PressureState)
    safety: SafetyState = dataclass_field(default_factory=SafetyState)
    backend: str = "mock"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = str(self.mode)
        data["temperature"]["sample"]["mode"] = str(self.temperature.sample.mode)
        data["temperature"]["vti"]["mode"] = str(self.temperature.vti.mode)
        return data
