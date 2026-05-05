from __future__ import annotations

from abc import ABC, abstractmethod
import re
import time
from time import monotonic

from .config import CryostatServiceConfig
from .state import CryostatMode, CryostatState, FieldState, PressureState, TemperatureState


class CryostatBackend(ABC):
    @abstractmethod
    def read_state(self) -> CryostatState:
        raise NotImplementedError

    @abstractmethod
    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def hold(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def abort(self) -> None:
        raise NotImplementedError

    def diagnostics(self) -> dict:
        return {"backend": type(self).__name__}

    def catalog(self) -> dict:
        return {}

    def raw_readings(self) -> dict:
        return self.read_state().to_dict()


class MockCryostatBackend(CryostatBackend):
    def __init__(self, config: CryostatServiceConfig):
        self.config = config
        self._temperature_K = 295.0
        self._target_temperature_K = 295.0
        self._temperature_rate_K_per_min = 1.0
        self._field_T = 0.0
        self._target_field_T = 0.0
        self._field_rate_T_per_min = 0.2
        self._mode = CryostatMode.IDLE
        self._last_update = monotonic()
        self._aborted = False

    def read_state(self) -> CryostatState:
        self._advance()
        temp_ramping = abs(self._temperature_K - self._target_temperature_K) > 1e-3
        field_ramping = abs(self._field_T - self._target_field_T) > 1e-4

        if self._aborted:
            mode = CryostatMode.ABORTED
        elif temp_ramping and field_ramping:
            mode = CryostatMode.RAMPING_T_AND_B
        elif temp_ramping:
            mode = CryostatMode.RAMPING_T
        elif field_ramping:
            mode = CryostatMode.RAMPING_B
        else:
            mode = self._mode if self._mode == CryostatMode.HOLDING else CryostatMode.IDLE

        return CryostatState(
            mode=mode,
            temperature=TemperatureState(
                probe_K=round(self._temperature_K, 5),
                vti_K=round(self._temperature_K + 0.015, 5),
                target_K=self._target_temperature_K,
                rate_K_per_min=self._temperature_rate_K_per_min,
                probe_heater_percent=15.0 if temp_ramping else 3.0,
                vti_heater_percent=12.0 if temp_ramping else 2.0,
                stable=not temp_ramping,
                ramping=temp_ramping,
            ),
            field=FieldState(
                B_T=round(self._field_T, 6),
                target_T=self._target_field_T,
                rate_T_per_min=self._field_rate_T_per_min,
                stable=not field_ramping,
                ramping=field_ramping,
            ),
            pressure=PressureState(mbar=8.0e-6, needle_valve_percent=0.0),
            backend="mock",
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        self._aborted = False
        self._target_temperature_K = target_K
        self._temperature_rate_K_per_min = abs(rate_K_per_min)
        self._mode = CryostatMode.RAMPING_T

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self._aborted = False
        self._target_field_T = target_T
        self._field_rate_T_per_min = abs(rate_T_per_min)
        self._mode = CryostatMode.RAMPING_B

    def hold(self) -> None:
        self._advance()
        self._target_temperature_K = self._temperature_K
        self._target_field_T = self._field_T
        self._mode = CryostatMode.HOLDING

    def abort(self) -> None:
        self.hold()
        self._aborted = True

    def diagnostics(self) -> dict:
        return {
            "backend": "mock",
            "itc_address": self.config.itc.address,
            "ips_address": self.config.ips.address,
            "message": "Mock backend is active; no hardware resources are open.",
        }

    def catalog(self) -> dict:
        return {
            "itc": [
                f"{self.config.itc.probe_loop}:TEMP",
                f"{self.config.itc.vti_loop}:TEMP",
                f"{self.config.itc.pressure}:PRES",
            ],
            "ips": [f"{self.config.ips.magnet_group}:PSU"],
        }

    def _advance(self) -> None:
        now = monotonic()
        dt = now - self._last_update
        self._last_update = now
        self._temperature_K = _step_towards(
            self._temperature_K,
            self._target_temperature_K,
            self._temperature_rate_K_per_min / 60.0 * dt,
        )
        self._field_T = _step_towards(
            self._field_T,
            self._target_field_T,
            self._field_rate_T_per_min / 60.0 * dt,
        )


def _step_towards(current: float, target: float, max_step: float) -> float:
    if max_step <= 0:
        return current
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + max_step * (1 if delta > 0 else -1)


class MercuryResource:
    def __init__(self, address: str):
        import pyvisa

        self.address = address
        self.resource_manager = pyvisa.ResourceManager()
        self.instrument = self.resource_manager.open_resource(
            address,
            read_termination="\n",
            write_termination="\n",
        )

    def query(self, command: str) -> str:
        return self.instrument.query(command)

    def set(self, command: str) -> str:
        # Mercury controllers answer SET commands; using query keeps buffers aligned.
        return self.instrument.query(command)


class MercuryCryostatBackend(CryostatBackend):
    def __init__(self, config: CryostatServiceConfig):
        self.config = config
        self.itc = MercuryResource(config.itc.address)
        self.ips = MercuryResource(config.ips.address)
        self._temperature_target_K: float | None = None
        self._temperature_rate_K_per_min: float | None = None
        self._field_target_T: float | None = None
        self._field_rate_T_per_min: float | None = None
        self._mode = CryostatMode.IDLE
        self._aborted = False

    def read_state(self) -> CryostatState:
        probe_K = self._read_itc_float(
            f"READ:DEV:{self.config.itc.probe_signal}:TEMP:SIG:TEMP?"
        )
        vti_K = self._read_itc_float(
            f"READ:DEV:{self.config.itc.vti_signal}:TEMP:SIG:TEMP?"
        )
        probe_target_K = self._read_itc_float(
            f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:TSET?"
        )
        vti_target_K = self._read_itc_float(
            f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:TSET?"
        )
        probe_rate = self._read_itc_float(
            f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:RSET?"
        )
        probe_heater = self._read_itc_float(
            f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:HSET?"
        )
        vti_heater = self._read_itc_float(
            f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:HSET?"
        )
        pressure = self._read_itc_float(
            f"READ:DEV:{self.config.itc.pressure}:PRES:SIG:PRES?"
        )
        needle = self._read_itc_float(
            f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:FSET?"
        )

        field_T = self._read_ips_float(
            f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FLD?"
        )
        field_target_T = self._read_ips_float(
            f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FSET?"
        )
        field_rate = self._read_ips_float(
            f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:RFLD?"
        )

        target_K = _mean_available(probe_target_K, vti_target_K, self._temperature_target_K)
        rate_K = _first_available(probe_rate, self._temperature_rate_K_per_min)
        temp_ramping = _outside_tolerance(probe_K, target_K, 0.05) or _outside_tolerance(
            vti_K, target_K, 0.05
        )
        field_ramping = _outside_tolerance(field_T, field_target_T, 0.005)

        if self._aborted:
            mode = CryostatMode.ABORTED
        elif temp_ramping and field_ramping:
            mode = CryostatMode.RAMPING_T_AND_B
        elif temp_ramping:
            mode = CryostatMode.RAMPING_T
        elif field_ramping:
            mode = CryostatMode.RAMPING_B
        else:
            mode = self._mode if self._mode == CryostatMode.HOLDING else CryostatMode.IDLE

        return CryostatState(
            mode=mode,
            temperature=TemperatureState(
                probe_K=probe_K,
                vti_K=vti_K,
                target_K=target_K,
                rate_K_per_min=rate_K,
                probe_heater_percent=probe_heater,
                vti_heater_percent=vti_heater,
                stable=not temp_ramping,
                ramping=temp_ramping,
            ),
            field=FieldState(
                B_T=field_T,
                target_T=field_target_T,
                rate_T_per_min=field_rate,
                stable=not field_ramping,
                ramping=field_ramping,
            ),
            pressure=PressureState(mbar=pressure, needle_valve_percent=needle),
            backend="mercury",
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        self._aborted = False
        self._temperature_target_K = target_K
        self._temperature_rate_K_per_min = rate_K_per_min
        for loop in {self.config.itc.probe_loop, self.config.itc.vti_loop}:
            self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:RENA:ON")
            self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:RSET:{rate_K_per_min:.9g}")
            self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:TSET:{target_K:.9g}")
            self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:ENAB:ON")
        self._mode = CryostatMode.RAMPING_T

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self._aborted = False
        self._field_target_T = target_T
        self._field_rate_T_per_min = rate_T_per_min
        group = self.config.ips.magnet_group
        self.ips.set(f"SET:DEV:{group}:PSU:ACTN:HOLD")
        time.sleep(0.1)
        self.ips.set(f"SET:DEV:{group}:PSU:SIG:RFST:{rate_T_per_min:.9g}")
        time.sleep(0.1)
        self.ips.set(f"SET:DEV:{group}:PSU:SIG:FSET:{target_T:.9g}")
        time.sleep(0.1)
        self.ips.set(f"SET:DEV:{group}:PSU:ACTN:RTOS")
        self._mode = CryostatMode.RAMPING_B

    def hold(self) -> None:
        state = self.read_state()
        if state.temperature.probe_K is not None:
            for loop in {self.config.itc.probe_loop, self.config.itc.vti_loop}:
                self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:RENA:OFF")
                self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:TSET:{state.temperature.probe_K:.9g}")
                self.itc.set(f"SET:DEV:{loop}:TEMP:LOOP:ENAB:ON")
        self.ips.set(f"SET:DEV:{self.config.ips.magnet_group}:PSU:ACTN:HOLD")
        self._mode = CryostatMode.HOLDING

    def abort(self) -> None:
        self.hold()
        self._aborted = True

    def diagnostics(self) -> dict:
        return {
            "backend": "mercury",
            "itc_address": self.config.itc.address,
            "ips_address": self.config.ips.address,
            "itc_modules": {
                "probe_signal": self.config.itc.probe_signal,
                "probe_loop": self.config.itc.probe_loop,
                "vti_signal": self.config.itc.vti_signal,
                "vti_loop": self.config.itc.vti_loop,
                "pressure": self.config.itc.pressure,
            },
            "ips_modules": {
                "magnet_group": self.config.ips.magnet_group,
            },
        }

    def catalog(self) -> dict:
        return {
            "itc": self.itc.query("READ:SYS:CAT"),
            "ips": self.ips.query("READ:SYS:CAT"),
        }

    def raw_readings(self) -> dict:
        commands = {
            "itc_probe_temp": f"READ:DEV:{self.config.itc.probe_signal}:TEMP:SIG:TEMP?",
            "itc_vti_temp": f"READ:DEV:{self.config.itc.vti_signal}:TEMP:SIG:TEMP?",
            "itc_probe_setpoint": f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:TSET?",
            "itc_vti_setpoint": f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:TSET?",
            "itc_pressure": f"READ:DEV:{self.config.itc.pressure}:PRES:SIG:PRES?",
            "itc_needle_valve": f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:FSET?",
            "ips_field": f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FLD?",
            "ips_field_setpoint": f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FSET?",
            "ips_field_rate": f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:RFLD?",
        }
        return {
            name: {
                "command": command,
                "response": self._query_for_diagnostics(command),
            }
            for name, command in commands.items()
        }

    def _query_for_diagnostics(self, command: str) -> str:
        if command.startswith("READ:DEV:") and ":PSU:" in command:
            return self.ips.query(command)
        return self.itc.query(command)

    def _read_itc_float(self, command: str) -> float | None:
        return _extract_float(self.itc.query(command))

    def _read_ips_float(self, command: str) -> float | None:
        return _extract_float(self.ips.query(command))


def _extract_float(response: str) -> float | None:
    matches = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", response)
    if not matches:
        return None
    return float(matches[-1])


def _first_available(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _mean_available(*values: float | None) -> float | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def _outside_tolerance(value: float | None, target: float | None, tolerance: float) -> bool:
    if value is None or target is None:
        return False
    return abs(value - target) > tolerance


def create_backend(config: CryostatServiceConfig) -> CryostatBackend:
    if config.backend == "mock":
        return MockCryostatBackend(config)
    if config.backend == "mercury":
        return MercuryCryostatBackend(config)
    raise ValueError(f"Unsupported cryostat backend: {config.backend}")


def list_visa_resources() -> dict:
    try:
        import pyvisa
    except ImportError as exc:
        return {"ok": False, "error": f"pyvisa is not installed: {exc}"}

    try:
        resource_manager = pyvisa.ResourceManager()
        return {"ok": True, "resources": list(resource_manager.list_resources())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
