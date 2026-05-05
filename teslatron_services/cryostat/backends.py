from __future__ import annotations

from abc import ABC, abstractmethod
import re
import time
from time import monotonic, time as unix_time

from .config import CryostatServiceConfig
from .state import (
    CryostatMode,
    CryostatState,
    FieldState,
    GasControlMode,
    PIDState,
    PressureState,
    TemperatureControlMode,
    TemperatureLoopState,
    TemperatureState,
)


class CryostatBackend(ABC):
    @abstractmethod
    def read_state(self) -> CryostatState:
        raise NotImplementedError

    @abstractmethod
    def ramp_temperature(
        self,
        target_K: float,
        rate_K_per_min: float,
        loop: str = "both",
    ) -> None:
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

    def set_vti_needle(self, needle_valve_percent: float) -> None:
        raise NotImplementedError

    def set_vti_pressure(self, pressure_mbar: float) -> None:
        raise NotImplementedError

    def diagnostics(self) -> dict:
        return {"backend": type(self).__name__}

    def catalog(self) -> dict:
        return {}

    def raw_readings(self) -> dict:
        return self.read_state().to_dict()

    def diagnostic_query(self, target: str, command: str) -> dict:
        raise NotImplementedError


class MockCryostatBackend(CryostatBackend):
    def __init__(self, config: CryostatServiceConfig):
        self.config = config
        self._sample_temperature_K = 295.0
        self._sample_target_K = 295.0
        self._sample_rate_K_per_min = 1.0
        self._sample_mode = TemperatureControlMode.FIXED_TARGET
        self._vti_temperature_K = 295.015
        self._vti_target_K = 295.015
        self._vti_rate_K_per_min = 1.0
        self._vti_mode = TemperatureControlMode.FIXED_TARGET
        self._field_T = 0.0
        self._target_field_T = 0.0
        self._field_rate_T_per_min = 0.2
        self._pressure_mbar = 8.0e-6
        self._pressure_target_mbar: float | None = None
        self._needle_valve_percent = 0.0
        self._gas_mode = GasControlMode.FIXED_NEEDLE
        self._mode = CryostatMode.IDLE
        self._last_update = monotonic()
        self._aborted = False

    def read_state(self) -> CryostatState:
        self._advance()
        sample_ramping = abs(self._sample_temperature_K - self._sample_target_K) > 1e-3
        vti_ramping = abs(self._vti_temperature_K - self._vti_target_K) > 1e-3
        temp_ramping = sample_ramping or vti_ramping
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
                sample=TemperatureLoopState(
                    temperature_K=round(self._sample_temperature_K, 5),
                    target_K=self._sample_target_K,
                    rate_K_per_min=self._sample_rate_K_per_min,
                    ramp_end_K=self._sample_target_K,
                    heater_percent=15.0 if sample_ramping else 3.0,
                    heater_power_W=0.8 if sample_ramping else 0.1,
                    mode=self._sample_mode,
                    pid=PIDState(mode="AUTO", p=10.0, i=1.0, d=0.0),
                    stable=not sample_ramping,
                    ramping=sample_ramping,
                ),
                vti=TemperatureLoopState(
                    temperature_K=round(self._vti_temperature_K, 5),
                    target_K=self._vti_target_K,
                    rate_K_per_min=self._vti_rate_K_per_min,
                    ramp_end_K=self._vti_target_K,
                    heater_percent=12.0 if vti_ramping else 2.0,
                    heater_power_W=3.0 if vti_ramping else 0.2,
                    mode=self._vti_mode,
                    pid=PIDState(mode="AUTO", p=25.0, i=1.0, d=0.0),
                    stable=not vti_ramping,
                    ramping=vti_ramping,
                ),
            ),
            field=FieldState(
                B_T=round(self._field_T, 6),
                target_T=self._target_field_T,
                rate_T_per_min=self._field_rate_T_per_min,
                stable=not field_ramping,
                ramping=field_ramping,
            ),
            pressure=PressureState(
                mbar=self._pressure_mbar,
                target_mbar=self._pressure_target_mbar,
                needle_valve_percent=self._needle_valve_percent,
                mode=self._gas_mode,
            ),
            backend="mock",
        )

    def ramp_temperature(
        self,
        target_K: float,
        rate_K_per_min: float,
        loop: str = "both",
    ) -> None:
        self._aborted = False
        if loop in {"sample", "both"}:
            self._sample_target_K = target_K
            self._sample_rate_K_per_min = abs(rate_K_per_min)
            self._sample_mode = TemperatureControlMode.RAMP
        if loop in {"vti", "both"}:
            self._vti_target_K = target_K
            self._vti_rate_K_per_min = abs(rate_K_per_min)
            self._vti_mode = TemperatureControlMode.RAMP
        self._mode = CryostatMode.RAMPING_T

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self._aborted = False
        self._target_field_T = target_T
        self._field_rate_T_per_min = abs(rate_T_per_min)
        self._mode = CryostatMode.RAMPING_B

    def hold(self) -> None:
        self._advance()
        self._sample_target_K = self._sample_temperature_K
        self._vti_target_K = self._vti_temperature_K
        self._target_field_T = self._field_T
        self._mode = CryostatMode.HOLDING

    def abort(self) -> None:
        self.hold()
        self._aborted = True

    def set_vti_needle(self, needle_valve_percent: float) -> None:
        self._needle_valve_percent = needle_valve_percent
        self._gas_mode = GasControlMode.FIXED_NEEDLE

    def set_vti_pressure(self, pressure_mbar: float) -> None:
        self._pressure_target_mbar = pressure_mbar
        self._gas_mode = GasControlMode.PRESSURE_CONTROL

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

    def diagnostic_query(self, target: str, command: str) -> dict:
        return {
            "target": target,
            "command": command,
            "response": "MOCK:OK",
            "backend": "mock",
        }

    def _advance(self) -> None:
        now = monotonic()
        dt = now - self._last_update
        self._last_update = now
        self._sample_temperature_K = _step_towards(
            self._sample_temperature_K,
            self._sample_target_K,
            self._sample_rate_K_per_min / 60.0 * dt,
        )
        self._vti_temperature_K = _step_towards(
            self._vti_temperature_K,
            self._vti_target_K,
            self._vti_rate_K_per_min / 60.0 * dt,
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
    def __init__(
        self,
        address: str,
        timeout_ms: int = 3000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ):
        import pyvisa

        self.address = address
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.resource_manager = pyvisa.ResourceManager()
        self.instrument = self.resource_manager.open_resource(
            address,
            read_termination=read_termination,
            write_termination=write_termination,
        )
        self.instrument.timeout = timeout_ms

    def query(self, command: str) -> str:
        try:
            return self.instrument.query(command)
        except Exception as exc:
            raise MercuryQueryError(self.address, command, exc) from exc

    def set(self, command: str) -> str:
        # Mercury controllers answer SET commands; using query keeps buffers aligned.
        return self.query(command)


class MercuryQueryError(RuntimeError):
    def __init__(self, address: str, command: str, original: Exception):
        self.address = address
        self.command = command
        self.timestamp = unix_time()
        self.original_type = type(original).__name__
        self.original_message = str(original)
        super().__init__(
            f"Mercury query failed at {address} for {command!r}: "
            f"{self.original_type}: {self.original_message}"
        )

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "command": self.command,
            "timestamp": self.timestamp,
            "exception_type": self.original_type,
            "message": self.original_message,
        }


class MercuryCryostatBackend(CryostatBackend):
    def __init__(self, config: CryostatServiceConfig):
        self.config = config
        self.itc = MercuryResource(
            config.itc.address,
            timeout_ms=config.itc.timeout_ms,
            read_termination=config.itc.read_termination,
            write_termination=config.itc.write_termination,
        )
        self.ips = MercuryResource(
            config.ips.address,
            timeout_ms=config.ips.timeout_ms,
            read_termination=config.ips.read_termination,
            write_termination=config.ips.write_termination,
        )
        self._sample_target_K: float | None = None
        self._sample_rate_K_per_min: float | None = None
        self._vti_target_K: float | None = None
        self._vti_rate_K_per_min: float | None = None
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
        pressure_target = self._read_itc_float(
            f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:PRST?"
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

        vti_rate = self._read_itc_float(
            f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:RSET?"
        )
        sample_ramping = _outside_tolerance(probe_K, probe_target_K, 0.05)
        vti_ramping = _outside_tolerance(vti_K, vti_target_K, 0.05)
        temp_ramping = sample_ramping or vti_ramping
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
                sample=TemperatureLoopState(
                    temperature_K=probe_K,
                    target_K=probe_target_K,
                    rate_K_per_min=_first_available(probe_rate, self._sample_rate_K_per_min),
                    ramp_end_K=probe_target_K,
                    heater_percent=probe_heater,
                    mode=TemperatureControlMode.RAMP if sample_ramping else TemperatureControlMode.FIXED_TARGET,
                    stable=not sample_ramping,
                    ramping=sample_ramping,
                ),
                vti=TemperatureLoopState(
                    temperature_K=vti_K,
                    target_K=vti_target_K,
                    rate_K_per_min=_first_available(vti_rate, self._vti_rate_K_per_min),
                    ramp_end_K=vti_target_K,
                    heater_percent=vti_heater,
                    mode=TemperatureControlMode.RAMP if vti_ramping else TemperatureControlMode.FIXED_TARGET,
                    stable=not vti_ramping,
                    ramping=vti_ramping,
                ),
            ),
            field=FieldState(
                B_T=field_T,
                target_T=field_target_T,
                rate_T_per_min=field_rate,
                stable=not field_ramping,
                ramping=field_ramping,
            ),
            pressure=PressureState(
                mbar=pressure,
                target_mbar=pressure_target,
                needle_valve_percent=needle,
                mode=GasControlMode.PRESSURE_CONTROL
                if pressure_target is not None
                else GasControlMode.UNKNOWN,
            ),
            backend="mercury",
        )

    def ramp_temperature(
        self,
        target_K: float,
        rate_K_per_min: float,
        loop: str = "both",
    ) -> None:
        self._aborted = False
        if loop in {"sample", "both"}:
            self._sample_target_K = target_K
            self._sample_rate_K_per_min = rate_K_per_min
        if loop in {"vti", "both"}:
            self._vti_target_K = target_K
            self._vti_rate_K_per_min = rate_K_per_min
        for mercury_loop in self._temperature_loop_names(loop):
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RENA:ON")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RSET:{rate_K_per_min:.9g}")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:TSET:{target_K:.9g}")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:ENAB:ON")
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
        current_targets = {
            self.config.itc.probe_loop: state.temperature.sample.temperature_K,
            self.config.itc.vti_loop: state.temperature.vti.temperature_K,
        }
        for mercury_loop, target_K in current_targets.items():
            if target_K is None:
                continue
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RENA:OFF")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:TSET:{target_K:.9g}")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:ENAB:ON")
        self.ips.set(f"SET:DEV:{self.config.ips.magnet_group}:PSU:ACTN:HOLD")
        self._mode = CryostatMode.HOLDING

    def abort(self) -> None:
        self.hold()
        self._aborted = True

    def set_vti_needle(self, needle_valve_percent: float) -> None:
        self.itc.set(
            f"SET:DEV:{self.config.itc.pressure}:PRES:LOOP:FSET:{needle_valve_percent:.9g}"
        )

    def set_vti_pressure(self, pressure_mbar: float) -> None:
        self.itc.set(f"SET:DEV:{self.config.itc.pressure}:PRES:LOOP:ENAB:ON")
        self.itc.set(
            f"SET:DEV:{self.config.itc.pressure}:PRES:LOOP:PRST:{pressure_mbar:.9g}"
        )

    def diagnostics(self) -> dict:
        return {
            "backend": "mercury",
            "itc_address": self.config.itc.address,
            "ips_address": self.config.ips.address,
            "itc_visa": {
                "timeout_ms": self.config.itc.timeout_ms,
                "read_termination": self.config.itc.read_termination,
                "write_termination": self.config.itc.write_termination,
            },
            "ips_visa": {
                "timeout_ms": self.config.ips.timeout_ms,
                "read_termination": self.config.ips.read_termination,
                "write_termination": self.config.ips.write_termination,
            },
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
            "itc_pressure_setpoint": f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:PRST?",
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

    def diagnostic_query(self, target: str, command: str) -> dict:
        resource = self._diagnostic_resource(target)
        try:
            response = resource.query(command)
            return {
                "target": target,
                "address": resource.address,
                "command": command,
                "response": response,
            }
        except MercuryQueryError as exc:
            return {
                "target": target,
                "address": resource.address,
                "command": command,
                "error": exc.to_dict(),
            }

    def _query_for_diagnostics(self, command: str) -> str:
        if command.startswith("READ:DEV:") and ":PSU:" in command:
            return self.ips.query(command)
        return self.itc.query(command)

    def _diagnostic_resource(self, target: str) -> MercuryResource:
        match target:
            case "itc":
                return self.itc
            case "ips":
                return self.ips
            case _:
                raise ValueError("Diagnostic target must be 'itc' or 'ips'")

    def _temperature_loop_names(self, loop: str) -> set[str]:
        match loop:
            case "sample":
                return {self.config.itc.probe_loop}
            case "vti":
                return {self.config.itc.vti_loop}
            case "both":
                return {self.config.itc.probe_loop, self.config.itc.vti_loop}
            case _:
                raise ValueError("Temperature loop must be 'sample', 'vti', or 'both'")

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
