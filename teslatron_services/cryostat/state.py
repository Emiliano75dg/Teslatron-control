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


@dataclass(slots=True)
class TemperatureState:
    probe_K: float | None = None
    vti_K: float | None = None
    target_K: float | None = None
    rate_K_per_min: float | None = None
    probe_heater_percent: float | None = None
    vti_heater_percent: float | None = None
    stable: bool = False
    ramping: bool = False


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
        return data
