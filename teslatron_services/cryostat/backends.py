from __future__ import annotations

import logging
import re
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import monotonic
from time import time as unix_time

from .config import CryostatServiceConfig, MercurySensorSetupConfig
from .state import (
    CryostatMode,
    CryostatState,
    FieldState,
    GasControlMode,
    MagnetAction,
    PIDState,
    PressureState,
    SafetyState,
    SwitchHeaterState,
    SwitchHeaterStatus,
    TemperatureControlMode,
    TemperatureLoopState,
    TemperatureState,
)

LOW_FIELD_RATE_LIMIT_T_PER_MIN = 0.15
LOW_FIELD_RATE_WINDOW_T = 1.0

logger = logging.getLogger(__name__)


class CryostatBackend(ABC):
    def close(self) -> None:
        return None

    def apply_sample_sensor(self, sensor: MercurySensorSetupConfig) -> None:
        return None

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
    def set_temperature_target(
        self,
        target_K: float,
        loop: str = "both",
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def ramp_to_zero(self, rate_T_per_min: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def clamp(self) -> None:
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

    def set_temperature_fixed_heater(self, loop: str, heater_percent: float) -> None:
        raise NotImplementedError

    def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> None:
        raise NotImplementedError

    def set_switch_heater(self, enabled: bool) -> None:
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
        self._sample_heater_percent = 3.0
        self._sample_pid = PIDState(mode="AUTO", p=10.0, i=1.0, d=0.0)
        self._vti_temperature_K = 295.015
        self._vti_target_K = 295.015
        self._vti_rate_K_per_min = 1.0
        self._vti_mode = TemperatureControlMode.FIXED_TARGET
        self._vti_heater_percent = 2.0
        self._vti_pid = PIDState(mode="AUTO", p=25.0, i=1.0, d=0.0)
        self._field_T = 0.0
        self._target_field_T = 0.0
        self._field_rate_T_per_min = 0.2
        self._field_requested_rate_T_per_min = 0.2
        self._field_current_A = 0.0
        self._field_voltage_V = 0.0
        self._field_action = MagnetAction.HOLD
        self._switch_heater_status = SwitchHeaterStatus.OFF
        self._switch_heater_target = SwitchHeaterStatus.OFF
        self._switch_heater_changed_at: float | None = None
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
        field_at_zero = abs(self._field_T) <= 1e-4

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
                    heater_percent=15.0 if sample_ramping else self._sample_heater_percent,
                    heater_power_W=0.8 if sample_ramping else 0.1,
                    heater_mode=str(self._sample_mode),
                    loop_enabled=self._sample_mode != TemperatureControlMode.FIXED_HEATER,
                    ramp_enabled=self._sample_mode == TemperatureControlMode.RAMP,
                    target_reached=not sample_ramping,
                    mode=self._sample_mode,
                    pid=self._sample_pid,
                    stable=not sample_ramping,
                    ramping=sample_ramping,
                ),
                vti=TemperatureLoopState(
                    temperature_K=round(self._vti_temperature_K, 5),
                    target_K=self._vti_target_K,
                    rate_K_per_min=self._vti_rate_K_per_min,
                    ramp_end_K=self._vti_target_K,
                    heater_percent=12.0 if vti_ramping else self._vti_heater_percent,
                    heater_power_W=3.0 if vti_ramping else 0.2,
                    heater_mode=str(self._vti_mode),
                    loop_enabled=self._vti_mode != TemperatureControlMode.FIXED_HEATER,
                    ramp_enabled=self._vti_mode == TemperatureControlMode.RAMP,
                    target_reached=not vti_ramping,
                    mode=self._vti_mode,
                    pid=self._vti_pid,
                    stable=not vti_ramping,
                    ramping=vti_ramping,
                ),
            ),
            field=FieldState(
                B_T=round(self._field_T, 6),
                target_T=self._target_field_T,
                rate_T_per_min=self._field_rate_T_per_min,
                output_current_A=round(self._field_current_A, 6),
                output_voltage_V=round(self._field_voltage_V, 6),
                magnet_temperature_K=4.2,
                pt1_temperature_K=3.82,
                pt2_temperature_K=50.8,
                action=self._field_action
                if self._field_action == MagnetAction.CLAMP
                else (MagnetAction.TO_SET if field_ramping else MagnetAction.HOLD),
                at_setpoint=not field_ramping,
                at_zero=field_at_zero,
                clamped=self._field_action == MagnetAction.CLAMP,
                stable=not field_ramping,
                ramping=field_ramping,
            ),
            switch_heater=self._switch_heater_state(),
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
        targets = _temperature_targets_for_loop(
            loop,
            target_K,
            sample_loop=self.config.itc.probe_loop,
            vti_loop=self.config.itc.vti_loop,
        )
        if loop in {"sample", "both"}:
            self._sample_target_K = targets[self.config.itc.probe_loop]
            self._sample_rate_K_per_min = abs(rate_K_per_min)
            self._sample_mode = TemperatureControlMode.RAMP
        if loop in {"vti", "both"}:
            self._vti_target_K = targets[self.config.itc.vti_loop]
            self._vti_rate_K_per_min = abs(rate_K_per_min)
            self._vti_mode = TemperatureControlMode.RAMP
        self._mode = CryostatMode.RAMPING_T

    def set_temperature_target(
        self,
        target_K: float,
        loop: str = "both",
    ) -> None:
        self._aborted = False
        targets = _temperature_targets_for_loop(
            loop,
            target_K,
            sample_loop=self.config.itc.probe_loop,
            vti_loop=self.config.itc.vti_loop,
        )
        if loop in {"sample", "both"}:
            self._sample_target_K = targets[self.config.itc.probe_loop]
            self._sample_mode = TemperatureControlMode.FIXED_TARGET
        if loop in {"vti", "both"}:
            self._vti_target_K = targets[self.config.itc.vti_loop]
            self._vti_mode = TemperatureControlMode.FIXED_TARGET
        self._mode = CryostatMode.HOLDING

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self._aborted = False
        self._target_field_T = target_T
        self._field_requested_rate_T_per_min = abs(rate_T_per_min)
        self._field_rate_T_per_min = _field_rate_with_low_field_cap(
            self._field_T,
            self._field_requested_rate_T_per_min,
        )
        self._field_action = MagnetAction.TO_SET
        self._mode = CryostatMode.RAMPING_B

    def ramp_to_zero(self, rate_T_per_min: float) -> None:
        self._aborted = False
        self._target_field_T = 0.0
        self._field_requested_rate_T_per_min = abs(rate_T_per_min)
        self._field_rate_T_per_min = _field_rate_with_low_field_cap(
            self._field_T,
            self._field_requested_rate_T_per_min,
        )
        self._field_action = MagnetAction.TO_ZERO
        self._mode = CryostatMode.RAMPING_B

    def clamp(self) -> None:
        self._advance()
        if abs(self._field_current_A) >= 1.0:
            raise PermissionError("Clamp blocked: magnet output current must be below 1 A")
        self._target_field_T = self._field_T
        self._field_action = MagnetAction.CLAMP
        self._mode = CryostatMode.HOLDING

    def hold(self) -> None:
        self._advance()
        self._sample_target_K = self._sample_temperature_K
        self._vti_target_K = self._vti_temperature_K
        self._target_field_T = self._field_T
        self._sample_mode = TemperatureControlMode.FIXED_TARGET
        self._vti_mode = TemperatureControlMode.FIXED_TARGET
        self._field_action = MagnetAction.HOLD
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

    def set_temperature_fixed_heater(self, loop: str, heater_percent: float) -> None:
        self._aborted = False
        if loop in {"sample", "both"}:
            self._sample_mode = TemperatureControlMode.FIXED_HEATER
            self._sample_heater_percent = heater_percent
        if loop in {"vti", "both"}:
            self._vti_mode = TemperatureControlMode.FIXED_HEATER
            self._vti_heater_percent = heater_percent
        self._mode = CryostatMode.HOLDING

    def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> None:
        if loop in {"sample", "both"}:
            self._sample_mode = (
                TemperatureControlMode.PID_AUTO if auto else TemperatureControlMode.PID_USER
            )
            self._sample_pid = PIDState(mode="AUTO" if auto else "USER", p=p, i=i, d=d)
        if loop in {"vti", "both"}:
            self._vti_mode = (
                TemperatureControlMode.PID_AUTO if auto else TemperatureControlMode.PID_USER
            )
            self._vti_pid = PIDState(mode="AUTO" if auto else "USER", p=p, i=i, d=d)

    def set_switch_heater(self, enabled: bool) -> None:
        status = SwitchHeaterStatus.ON if enabled else SwitchHeaterStatus.OFF
        self._switch_heater_status = status
        self._switch_heater_target = status
        self._switch_heater_changed_at = unix_time()

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

    def _switch_heater_state(self) -> SwitchHeaterState:
        delay = self._switch_heater_delay_s(self._switch_heater_target)
        elapsed = (
            unix_time() - self._switch_heater_changed_at
            if self._switch_heater_changed_at is not None
            else None
        )
        return SwitchHeaterState(
            status=self._switch_heater_status,
            target_status=self._switch_heater_target,
            ready=elapsed is None or elapsed >= delay,
            delay_s=delay,
            last_changed_at=self._switch_heater_changed_at,
            elapsed_s=elapsed,
        )

    def _switch_heater_delay_s(self, status: SwitchHeaterStatus) -> float:
        if status == SwitchHeaterStatus.ON:
            return self.config.ips.switch_on_delay_s
        return self.config.ips.switch_off_delay_s

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
            _field_rate_with_low_field_cap(
                self._field_T,
                self._field_requested_rate_T_per_min,
            )
            / 60.0
            * dt,
        )
        self._field_rate_T_per_min = _field_rate_with_low_field_cap(
            self._field_T,
            self._field_requested_rate_T_per_min,
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
        self.address = address
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.socket_endpoint = self._parse_socket_address(address)
        self.socket_connection: socket.socket | None = None
        self._last_socket_query_at = 0.0
        self._query_lock = threading.Lock()
        self.resource_manager = None
        self.instrument = None
        if self.socket_endpoint is None:
            import pyvisa

            self.resource_manager = pyvisa.ResourceManager()
            self.instrument = self.resource_manager.open_resource(
                address,
                read_termination=read_termination,
                write_termination=write_termination,
            )
            self.instrument.timeout = timeout_ms

    def query(self, command: str) -> str:
        with self._query_lock:
            try:
                if self.socket_endpoint is not None:
                    return self._socket_query(command)
                if self.instrument is None:
                    raise RuntimeError("VISA instrument is not open")
                return self.instrument.query(command)
            except Exception as exc:
                raise MercuryQueryError(self.address, command, exc) from exc

    def set(self, command: str) -> str:
        # Mercury controllers answer SET commands; using query keeps buffers aligned.
        return self.query(command)

    def _socket_query(self, command: str) -> str:
        self._respect_message_interval()
        try:
            return self._socket_query_once(command)
        except OSError:
            self._close_socket_connection()
            return self._socket_query_once(command)

    def _socket_query_once(self, command: str) -> str:
        connection = self._socket()
        timeout_s = self.timeout_ms / 1000.0
        write_termination = self.write_termination.encode()
        read_termination = self.read_termination.encode()
        payload = command.encode() + write_termination
        connection.settimeout(timeout_s)
        connection.sendall(payload)
        self._last_socket_query_at = monotonic()
        chunks = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionResetError("Mercury socket closed before response")
            chunks.append(chunk)
            response = b"".join(chunks)
            if read_termination and response.endswith(read_termination):
                break
        return response.decode(errors="replace")

    def _socket(self) -> socket.socket:
        if self.socket_connection is None:
            if self.socket_endpoint is None:
                raise RuntimeError("Socket endpoint is not configured")
            host, port = self.socket_endpoint
            timeout_s = self.timeout_ms / 1000.0
            self.socket_connection = socket.create_connection((host, port), timeout=timeout_s)
        return self.socket_connection

    def _close_socket_connection(self) -> None:
        if self.socket_connection is None:
            return
        connection = self.socket_connection
        self.socket_connection = None
        try:
            connection.close()
        except OSError:
            logger.warning("Error closing Mercury socket connection for %s", self.address)

    def _respect_message_interval(self) -> None:
        elapsed = monotonic() - self._last_socket_query_at
        if elapsed < 0.005:
            time.sleep(0.005 - elapsed)

    def close(self) -> None:
        self._close_socket_connection()
        address = getattr(self, "address", "<unknown>")
        instrument = self.instrument
        self.instrument = None
        if instrument is not None:
            try:
                instrument.close()
            except Exception as exc:
                logger.warning("Error closing Mercury instrument for %s: %s", address, exc)
        resource_manager = self.resource_manager
        self.resource_manager = None
        if resource_manager is not None:
            try:
                resource_manager.close()
            except Exception as exc:
                logger.warning(
                    "Error closing VISA resource manager for %s: %s",
                    address,
                    exc,
                )

    @staticmethod
    def _parse_socket_address(address: str) -> tuple[str, int] | None:
        match = re.fullmatch(r"TCPIP\d*::([^:]+)::(\d+)::SOCKET", address)
        if match is None:
            return None
        return match.group(1), int(match.group(2))


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


@dataclass(slots=True)
class _SampleSnapshot:
    loop_state: TemperatureLoopState
    safety_state: SafetyState


@dataclass(slots=True)
class _VtiSnapshot:
    loop_state: TemperatureLoopState
    pressure_state: PressureState


@dataclass(slots=True)
class _FieldSnapshot:
    field_state: FieldState
    switch_heater_state: SwitchHeaterState


class _MercurySampleControlStrategy:
    def __init__(self, backend: "MercuryCryostatBackend"):
        self.backend = backend

    def read_snapshot(self) -> _SampleSnapshot:
        backend = self.backend
        probe_K = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.probe_signal}:TEMP:SIG:TEMP?"
        )
        probe_target_K = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.probe_loop}:TEMP:LOOP:TSET?"
        )
        probe_rate = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.probe_loop}:TEMP:LOOP:RSET?"
        )
        probe_loop_enabled = backend._try_read_itc_bool(
            f"READ:DEV:{backend.config.itc.probe_loop}:TEMP:LOOP:ENAB?"
        )
        probe_ramp_enabled = backend._try_read_itc_bool(
            f"READ:DEV:{backend.config.itc.probe_loop}:TEMP:LOOP:RENA?"
        )
        probe_heater = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.probe_loop}:TEMP:LOOP:HSET?"
        )
        probe_pid = backend._read_temperature_pid(backend.config.itc.probe_loop)
        sample_target_reached = _within_tolerance(probe_K, probe_target_K, 0.05)
        sample_ramping = bool(probe_ramp_enabled) and sample_target_reached is False
        return _SampleSnapshot(
            loop_state=TemperatureLoopState(
                temperature_K=probe_K,
                target_K=probe_target_K,
                rate_K_per_min=_first_available(probe_rate, backend._sample_rate_K_per_min),
                ramp_end_K=probe_target_K,
                heater_percent=probe_heater,
                heater_mode=_temperature_heater_mode(
                    probe_loop_enabled,
                    probe_ramp_enabled,
                ),
                loop_enabled=probe_loop_enabled,
                ramp_enabled=probe_ramp_enabled,
                target_reached=sample_target_reached,
                mode=TemperatureControlMode.RAMP
                if probe_ramp_enabled
                else TemperatureControlMode.FIXED_TARGET,
                pid=probe_pid,
                stable=sample_target_reached is True,
                ramping=sample_ramping,
            ),
            safety_state=SafetyState(),
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        backend = self.backend
        backend._sample_target_K = target_K
        backend._sample_rate_K_per_min = rate_K_per_min
        backend._set_temperature_loop_target(
            backend.config.itc.probe_loop,
            target_K,
            rate_K_per_min=rate_K_per_min,
            ramp=True,
        )

    def set_temperature_target(self, target_K: float) -> None:
        backend = self.backend
        backend._sample_target_K = target_K
        backend._set_temperature_loop_target(backend.config.itc.probe_loop, target_K)

    def hold(self) -> None:
        backend = self.backend
        probe_K = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.probe_signal}:TEMP:SIG:TEMP?"
        )
        if probe_K is None:
            return
        backend._hold_temperature_loop(backend.config.itc.probe_loop, probe_K)

    def set_fixed_heater(self, heater_percent: float) -> None:
        backend = self.backend
        backend._set_temperature_loop_fixed_heater(
            backend.config.itc.probe_loop,
            heater_percent,
        )

    def set_pid(self, p: float, i: float, d: float, auto: bool = False) -> None:
        backend = self.backend
        backend._set_temperature_loop_pid(
            backend.config.itc.probe_loop,
            p,
            i,
            d,
            auto=auto,
        )


class _GlobalVtiControl:
    def __init__(self, backend: "MercuryCryostatBackend"):
        self.backend = backend

    def read_snapshot(self) -> _VtiSnapshot:
        backend = self.backend
        vti_K = backend._read_itc_float(f"READ:DEV:{backend.config.itc.vti_signal}:TEMP:SIG:TEMP?")
        vti_target_K = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.vti_loop}:TEMP:LOOP:TSET?"
        )
        vti_rate = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.vti_loop}:TEMP:LOOP:RSET?"
        )
        vti_loop_enabled = backend._try_read_itc_bool(
            f"READ:DEV:{backend.config.itc.vti_loop}:TEMP:LOOP:ENAB?"
        )
        vti_ramp_enabled = backend._try_read_itc_bool(
            f"READ:DEV:{backend.config.itc.vti_loop}:TEMP:LOOP:RENA?"
        )
        vti_heater = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.vti_loop}:TEMP:LOOP:HSET?"
        )
        vti_pid = backend._read_temperature_pid(backend.config.itc.vti_loop)
        pressure = backend._read_itc_float(f"READ:DEV:{backend.config.itc.pressure}:PRES:SIG:PRES?")
        needle = backend._read_itc_float(f"READ:DEV:{backend.config.itc.pressure}:PRES:LOOP:FSET?")
        pressure_target = backend._read_itc_float(
            f"READ:DEV:{backend.config.itc.pressure}:PRES:LOOP:PRST?"
        )
        pressure_loop_enabled = backend._try_read_itc_bool(
            f"READ:DEV:{backend.config.itc.pressure}:PRES:LOOP:ENAB?"
        )
        vti_target_reached = _within_tolerance(vti_K, vti_target_K, 0.05)
        vti_ramping = bool(vti_ramp_enabled) and vti_target_reached is False
        pressure_mode = _pressure_mode_from_loop_state(
            pressure_loop_enabled,
            pressure_target,
            needle,
        )
        return _VtiSnapshot(
            loop_state=TemperatureLoopState(
                temperature_K=vti_K,
                target_K=vti_target_K,
                rate_K_per_min=_first_available(vti_rate, backend._vti_rate_K_per_min),
                ramp_end_K=vti_target_K,
                heater_percent=vti_heater,
                heater_mode=_temperature_heater_mode(
                    vti_loop_enabled,
                    vti_ramp_enabled,
                ),
                loop_enabled=vti_loop_enabled,
                ramp_enabled=vti_ramp_enabled,
                target_reached=vti_target_reached,
                mode=TemperatureControlMode.RAMP
                if vti_ramp_enabled
                else TemperatureControlMode.FIXED_TARGET,
                pid=vti_pid,
                stable=vti_target_reached is True,
                ramping=vti_ramping,
            ),
            pressure_state=PressureState(
                mbar=pressure,
                target_mbar=pressure_target,
                needle_valve_percent=needle,
                mode=pressure_mode,
            ),
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        backend = self.backend
        backend._vti_target_K = target_K
        backend._vti_rate_K_per_min = rate_K_per_min
        backend._set_temperature_loop_target(
            backend.config.itc.vti_loop,
            target_K,
            rate_K_per_min=rate_K_per_min,
            ramp=True,
        )

    def set_temperature_target(self, target_K: float) -> None:
        backend = self.backend
        backend._vti_target_K = target_K
        backend._set_temperature_loop_target(backend.config.itc.vti_loop, target_K)

    def hold(self) -> None:
        backend = self.backend
        vti_K = backend._read_itc_float(f"READ:DEV:{backend.config.itc.vti_signal}:TEMP:SIG:TEMP?")
        if vti_K is None:
            return
        backend._hold_temperature_loop(backend.config.itc.vti_loop, vti_K)

    def set_needle(self, needle_valve_percent: float) -> None:
        backend = self.backend
        backend.itc.set(f"SET:DEV:{backend.config.itc.pressure}:PRES:LOOP:ENAB:OFF")
        backend.itc.set(
            f"SET:DEV:{backend.config.itc.pressure}:PRES:LOOP:FSET:{needle_valve_percent:.9g}"
        )

    def set_pressure(self, pressure_mbar: float) -> None:
        backend = self.backend
        backend.itc.set(f"SET:DEV:{backend.config.itc.pressure}:PRES:LOOP:ENAB:ON")
        backend.itc.set(f"SET:DEV:{backend.config.itc.pressure}:PRES:LOOP:PRST:{pressure_mbar:.9g}")

    def set_fixed_heater(self, heater_percent: float) -> None:
        backend = self.backend
        backend._set_temperature_loop_fixed_heater(
            backend.config.itc.vti_loop,
            heater_percent,
        )

    def set_pid(self, p: float, i: float, d: float, auto: bool = False) -> None:
        backend = self.backend
        backend._set_temperature_loop_pid(
            backend.config.itc.vti_loop,
            p,
            i,
            d,
            auto=auto,
        )


class _GlobalFieldControl:
    def __init__(self, backend: "MercuryCryostatBackend"):
        self.backend = backend

    def read_snapshot(self) -> _FieldSnapshot:
        backend = self.backend
        field_T = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_group}:PSU:SIG:FLD?"
        )
        field_current_A = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_group}:PSU:SIG:CURR?"
        )
        field_voltage_V = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_group}:PSU:SIG:VOLT?"
        )
        field_target_T = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_group}:PSU:SIG:FSET?"
        )
        field_rate = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_group}:PSU:SIG:RFLD?"
        )
        magnet_action = backend._read_magnet_action()
        magnet_temperature_K = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.magnet_temperature}:TEMP:SIG:TEMP?"
        )
        pt1_temperature_K = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.pt1_temperature}:TEMP:SIG:TEMP?"
        )
        pt2_temperature_K = backend._read_ips_float(
            f"READ:DEV:{backend.config.ips.pt2_temperature}:TEMP:SIG:TEMP?"
        )
        switch_heater_status = backend._read_switch_heater_status()
        field_at_setpoint = _within_tolerance(field_T, field_target_T, 0.005)
        field_ramping = field_at_setpoint is False or magnet_action in {
            MagnetAction.TO_SET,
            MagnetAction.TO_ZERO,
        }
        if not backend.config.read_only:
            backend._maybe_adjust_field_rate(field_T, field_rate, field_ramping)
        return _FieldSnapshot(
            field_state=FieldState(
                B_T=field_T,
                target_T=field_target_T,
                rate_T_per_min=_first_available(backend._field_rate_T_per_min, field_rate),
                output_current_A=field_current_A,
                output_voltage_V=field_voltage_V,
                magnet_temperature_K=magnet_temperature_K,
                pt1_temperature_K=pt1_temperature_K,
                pt2_temperature_K=pt2_temperature_K,
                action=magnet_action,
                at_setpoint=field_at_setpoint,
                at_zero=_inside_tolerance(field_T, 0.0, 0.005),
                clamped=magnet_action == MagnetAction.CLAMP,
                stable=field_at_setpoint is True,
                ramping=field_ramping,
            ),
            switch_heater_state=backend._switch_heater_state(switch_heater_status),
        )

    def ramp(self, target_T: float, rate_T_per_min: float) -> None:
        backend = self.backend
        backend._ensure_switch_heater_ready_for_ramp()
        backend._field_target_T = target_T
        backend._field_requested_rate_T_per_min = rate_T_per_min
        group = backend.config.ips.magnet_group
        delay = backend.config.ips.command_delay_s
        current_field_T = backend._read_ips_float(f"READ:DEV:{group}:PSU:SIG:FLD?")
        effective_rate_T_per_min = _field_rate_with_low_field_cap(
            current_field_T,
            rate_T_per_min,
        )
        backend._field_rate_T_per_min = effective_rate_T_per_min
        backend.ips.set(f"SET:DEV:{group}:PSU:ACTN:HOLD")
        time.sleep(delay)
        backend.ips.set(f"SET:DEV:{group}:PSU:SIG:RFST:{effective_rate_T_per_min:.9g}")
        time.sleep(delay)
        backend.ips.set(f"SET:DEV:{group}:PSU:SIG:FSET:{target_T:.9g}")
        time.sleep(delay)
        backend.ips.set(f"SET:DEV:{group}:PSU:ACTN:RTOS")

    def ramp_to_zero(self, rate_T_per_min: float) -> None:
        backend = self.backend
        backend._ensure_switch_heater_ready_for_ramp()
        backend._field_target_T = 0.0
        backend._field_requested_rate_T_per_min = rate_T_per_min
        group = backend.config.ips.magnet_group
        delay = backend.config.ips.command_delay_s
        current_field_T = backend._read_ips_float(f"READ:DEV:{group}:PSU:SIG:FLD?")
        effective_rate_T_per_min = _field_rate_with_low_field_cap(
            current_field_T,
            rate_T_per_min,
        )
        backend._field_rate_T_per_min = effective_rate_T_per_min
        backend.ips.set(f"SET:DEV:{group}:PSU:ACTN:HOLD")
        time.sleep(delay)
        backend.ips.set(f"SET:DEV:{group}:PSU:SIG:RFST:{effective_rate_T_per_min:.9g}")
        time.sleep(delay)
        backend.ips.set(f"SET:DEV:{group}:PSU:ACTN:RTOZ")

    def clamp(self) -> None:
        backend = self.backend
        group = backend.config.ips.magnet_group
        current_A = backend._read_ips_float(f"READ:DEV:{group}:PSU:SIG:CURR?")
        if current_A is None:
            raise PermissionError("Clamp blocked: could not read magnet output current")
        if abs(current_A) >= 1.0:
            raise PermissionError(
                f"Clamp blocked: magnet output current is {current_A:.4g} A; "
                "manual allows clamp only below 1 A"
            )
        backend.ips.set(f"SET:DEV:{group}:PSU:ACTN:CLMP")
        backend._field_target_T = None
        backend._field_rate_T_per_min = None
        backend._field_requested_rate_T_per_min = None

    def hold(self) -> None:
        self.backend.ips.set(f"SET:DEV:{self.backend.config.ips.magnet_group}:PSU:ACTN:HOLD")

    def set_switch_heater(self, enabled: bool) -> None:
        backend = self.backend
        state = "ON" if enabled else "OFF"
        backend.ips.set(f"SET:DEV:{backend.config.ips.magnet_group}:PSU:SIG:SWHT:{state}")
        backend._switch_heater_target = SwitchHeaterStatus.ON if enabled else SwitchHeaterStatus.OFF
        backend._switch_heater_changed_at = unix_time()


class _HelioxSampleControlStrategy:
    def __init__(self, backend: "HelioxCryostatBackend"):
        self.backend = backend

    def read_snapshot(self) -> _SampleSnapshot:
        backend = self.backend
        sample_K = backend._try_read_heliox_float("READ:DEV:HelioxX:HEL:SIG:TEMP")
        sample_target_K = backend._try_read_heliox_float("READ:DEV:HelioxX:HEL:SIG:TSET")
        heliox_status = backend._try_read_heliox_status()
        sorb_temperature_K = backend._try_read_heliox_float("READ:DEV:HelioxX:HEL:SIG:SRBT")
        sorb_heater_percent = backend._try_read_heliox_float("READ:DEV:HelioxX:HEL:SIG:SRBH")
        sorb_stable = backend._read_heliox_bool("READ:DEV:HelioxX:HEL:SIG:SRBS")
        pot_stable = backend._read_heliox_bool("READ:DEV:HelioxX:HEL:SIG:H3PS")
        pot_heater_percent = backend._try_read_heliox_float("READ:DEV:HelioxX:HEL:SIG:H3PH")
        sample_target_reached = _within_tolerance(sample_K, sample_target_K, 0.05)
        sample_ramping = sample_target_reached is False
        control_channel = backend._heliox_control_channel(
            heliox_status,
            sorb_heater_percent=sorb_heater_percent,
            pot_heater_percent=pot_heater_percent,
        )
        if control_channel == "POT":
            sample_stable = (sample_target_reached is True) and (pot_stable is True)
        else:
            sample_stable = (sample_target_reached is True) and (sorb_stable is True)
        return _SampleSnapshot(
            loop_state=TemperatureLoopState(
                temperature_K=sample_K,
                target_K=sample_target_K,
                rate_K_per_min=backend._sample_requested_rate_K_per_min,
                ramp_end_K=sample_target_K,
                heater_percent=(
                    pot_heater_percent if control_channel == "POT" else sorb_heater_percent
                ),
                heater_mode=backend._heliox_heater_mode_label(heliox_status, control_channel),
                loop_enabled=True,
                ramp_enabled=sample_ramping,
                target_reached=sample_target_reached,
                mode=TemperatureControlMode.FIXED_TARGET,
                pid=PIDState(mode="HELIOX"),
                stable=sample_stable,
                ramping=sample_ramping,
            ),
            safety_state=SafetyState(
                message=(
                    None
                    if sorb_temperature_K is None
                    else (
                        f"Heliox sorb {sorb_temperature_K:.4g} K "
                        f"with inferred {control_channel.lower()} control"
                    )
                ),
            ),
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        backend = self.backend
        backend._sample_requested_rate_K_per_min = rate_K_per_min
        backend.itc.set(f"SET:DEV:HelioxX:HEL:TSET:{target_K:.9g}")
        backend._sample_target_K = target_K

    def set_temperature_target(self, target_K: float) -> None:
        backend = self.backend
        backend.itc.set(f"SET:DEV:HelioxX:HEL:TSET:{target_K:.9g}")
        backend._sample_target_K = target_K

    def hold(self) -> None:
        backend = self.backend
        sample_K = backend._read_heliox_float("READ:DEV:HelioxX:HEL:SIG:TEMP")
        if sample_K is None:
            raise PermissionError("Hold blocked: could not read Heliox temperature")
        backend.itc.set(f"SET:DEV:HelioxX:HEL:TSET:{sample_K:.9g}")
        backend._sample_target_K = sample_K


class MercuryCryostatBackend(CryostatBackend):
    BACKEND_NAME = "mercury"

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
        self._field_requested_rate_T_per_min: float | None = None
        self._switch_heater_target = SwitchHeaterStatus.UNKNOWN
        self._switch_heater_changed_at: float | None = None
        self._mode = CryostatMode.IDLE
        self._aborted = False

    def _sample_control_component(self) -> _MercurySampleControlStrategy:
        component = getattr(self, "_sample_control_cache", None)
        if component is None:
            component = _MercurySampleControlStrategy(self)
            self._sample_control_cache = component
        return component

    def _vti_control_component(self) -> _GlobalVtiControl:
        component = getattr(self, "_vti_control_cache", None)
        if component is None:
            component = _GlobalVtiControl(self)
            self._vti_control_cache = component
        return component

    def _field_control_component(self) -> _GlobalFieldControl:
        component = getattr(self, "_field_control_cache", None)
        if component is None:
            component = _GlobalFieldControl(self)
            self._field_control_cache = component
        return component

    def close(self) -> None:
        self.itc.close()
        self.ips.close()

    def _compose_state(
        self,
        sample_snapshot: _SampleSnapshot,
        vti_snapshot: _VtiSnapshot,
        field_snapshot: _FieldSnapshot,
    ) -> CryostatState:
        temp_ramping = sample_snapshot.loop_state.ramping or vti_snapshot.loop_state.ramping
        field_ramping = field_snapshot.field_state.ramping

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
                sample=sample_snapshot.loop_state,
                vti=vti_snapshot.loop_state,
            ),
            field=field_snapshot.field_state,
            switch_heater=field_snapshot.switch_heater_state,
            pressure=vti_snapshot.pressure_state,
            safety=sample_snapshot.safety_state,
            backend=self.BACKEND_NAME,
        )

    def _temperature_targets(self, loop: str, target_K: float) -> dict[str, float]:
        return _temperature_targets_for_loop(
            loop,
            target_K,
            sample_loop=self.config.itc.probe_loop,
            vti_loop=self.config.itc.vti_loop,
        )

    def apply_sample_sensor(self, sensor: MercurySensorSetupConfig) -> None:
        sensor_type = sensor.sensor_type.strip()
        excitation_type = sensor.excitation_type.strip()
        excitation_magnitude = sensor.excitation_magnitude.strip()
        calibration = sensor.calibration.strip()
        if not all((sensor_type, excitation_type, excitation_magnitude, calibration)):
            raise ValueError(
                "Selected sample sensor is incomplete; sensor type, excitation type, "
                "excitation magnitude, and calibration are all required"
            )
        probe_signal = self.config.itc.probe_signal
        self.itc.set(
            "SET:DEV:"
            f"{probe_signal}:TEMP:TYPE:{sensor_type}:"
            f"EXCT:TYPE:{excitation_type}:"
            f"MAG:{excitation_magnitude}:"
            f"CALB:{calibration}:DAT"
        )

    def read_state(self) -> CryostatState:
        sample_snapshot = self._sample_control_component().read_snapshot()
        vti_snapshot = self._vti_control_component().read_snapshot()
        field_snapshot = self._field_control_component().read_snapshot()
        return self._compose_state(sample_snapshot, vti_snapshot, field_snapshot)

    def ramp_temperature(
        self,
        target_K: float,
        rate_K_per_min: float,
        loop: str = "both",
    ) -> None:
        self._aborted = False
        targets = self._temperature_targets(loop, target_K)
        sample_target = targets.get(self.config.itc.probe_loop)
        if sample_target is not None:
            self._sample_control_component().ramp_temperature(sample_target, rate_K_per_min)
        vti_target = targets.get(self.config.itc.vti_loop)
        if vti_target is not None:
            self._vti_control_component().ramp_temperature(vti_target, rate_K_per_min)
        self._mode = CryostatMode.RAMPING_T

    def set_temperature_target(
        self,
        target_K: float,
        loop: str = "both",
    ) -> None:
        self._aborted = False
        targets = self._temperature_targets(loop, target_K)
        sample_target = targets.get(self.config.itc.probe_loop)
        if sample_target is not None:
            self._sample_control_component().set_temperature_target(sample_target)
        vti_target = targets.get(self.config.itc.vti_loop)
        if vti_target is not None:
            self._vti_control_component().set_temperature_target(vti_target)
        self._mode = CryostatMode.HOLDING

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self._aborted = False
        self._field_control_component().ramp(target_T, rate_T_per_min)
        self._mode = CryostatMode.RAMPING_B

    def ramp_to_zero(self, rate_T_per_min: float) -> None:
        self._aborted = False
        self._field_control_component().ramp_to_zero(rate_T_per_min)
        self._mode = CryostatMode.RAMPING_B

    def clamp(self) -> None:
        self._aborted = False
        self._field_control_component().clamp()
        self._mode = CryostatMode.HOLDING

    def hold(self) -> None:
        self._sample_control_component().hold()
        self._vti_control_component().hold()
        self._field_control_component().hold()
        self._mode = CryostatMode.HOLDING

    def abort(self) -> None:
        self.hold()
        self._aborted = True

    def set_vti_needle(self, needle_valve_percent: float) -> None:
        self._vti_control_component().set_needle(needle_valve_percent)

    def set_vti_pressure(self, pressure_mbar: float) -> None:
        self._vti_control_component().set_pressure(pressure_mbar)

    def set_temperature_fixed_heater(self, loop: str, heater_percent: float) -> None:
        if loop in {"sample", "both"}:
            self._sample_control_component().set_fixed_heater(heater_percent)
        if loop in {"vti", "both"}:
            self._vti_control_component().set_fixed_heater(heater_percent)
        self._mode = CryostatMode.HOLDING

    def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> None:
        if loop in {"sample", "both"}:
            self._sample_control_component().set_pid(p, i, d, auto=auto)
        if loop in {"vti", "both"}:
            self._vti_control_component().set_pid(p, i, d, auto=auto)

    def set_switch_heater(self, enabled: bool) -> None:
        self._field_control_component().set_switch_heater(enabled)

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
                "magnet_temperature": self.config.ips.magnet_temperature,
                "pt1_temperature": self.config.ips.pt1_temperature,
                "pt2_temperature": self.config.ips.pt2_temperature,
            },
            "switch_heater": {
                "on_delay_s": self.config.ips.switch_on_delay_s,
                "off_delay_s": self.config.ips.switch_off_delay_s,
                "normal_command": "SWHT",
                "forced_command_not_used": "SWHN",
                "ramp_blocked_during_transition": True,
            },
            "field_rate_override": {
                "window_min_T": -LOW_FIELD_RATE_WINDOW_T,
                "window_max_T": LOW_FIELD_RATE_WINDOW_T,
                "max_rate_T_per_min": LOW_FIELD_RATE_LIMIT_T_PER_MIN,
            },
        }

    def catalog(self) -> dict:
        return {
            "itc": self.itc.query("READ:SYS:CAT"),
            "ips": self.ips.query("READ:SYS:CAT"),
        }

    def raw_readings(self) -> dict:
        commands = {
            "itc_probe_temp": ("itc", f"READ:DEV:{self.config.itc.probe_signal}:TEMP:SIG:TEMP?"),
            "itc_vti_temp": ("itc", f"READ:DEV:{self.config.itc.vti_signal}:TEMP:SIG:TEMP?"),
            "itc_probe_setpoint": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:TSET?"),
            "itc_vti_setpoint": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:TSET?"),
            "itc_probe_rate": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:RSET?"),
            "itc_vti_rate": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:RSET?"),
            "itc_probe_pid_p": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:P?"),
            "itc_probe_pid_i": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:I?"),
            "itc_probe_pid_d": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:D?"),
            "itc_probe_pid_auto": ("itc", f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:PIDT?"),
            "itc_vti_pid_p": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:P?"),
            "itc_vti_pid_i": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:I?"),
            "itc_vti_pid_d": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:D?"),
            "itc_vti_pid_auto": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:PIDT?"),
            "itc_probe_loop_enabled": (
                "itc",
                f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:ENAB?",
            ),
            "itc_vti_loop_enabled": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:ENAB?"),
            "itc_probe_ramp_enabled": (
                "itc",
                f"READ:DEV:{self.config.itc.probe_loop}:TEMP:LOOP:RENA?",
            ),
            "itc_vti_ramp_enabled": ("itc", f"READ:DEV:{self.config.itc.vti_loop}:TEMP:LOOP:RENA?"),
            "itc_pressure": ("itc", f"READ:DEV:{self.config.itc.pressure}:PRES:SIG:PRES?"),
            "itc_pressure_loop_enabled": (
                "itc",
                f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:ENAB?",
            ),
            "itc_pressure_setpoint": (
                "itc",
                f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:PRST?",
            ),
            "itc_needle_valve": ("itc", f"READ:DEV:{self.config.itc.pressure}:PRES:LOOP:FSET?"),
            "ips_field": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FLD?"),
            "ips_current": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:CURR?"),
            "ips_voltage": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:VOLT?"),
            "ips_field_setpoint": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:FSET?"),
            "ips_field_rate": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:RFLD?"),
            "ips_action": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:ACTN?"),
            "ips_switch_heater": ("ips", f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:SWHT?"),
            "ips_magnet_temperature": (
                "ips",
                f"READ:DEV:{self.config.ips.magnet_temperature}:TEMP:SIG:TEMP?",
            ),
            "ips_pt1_temperature": (
                "ips",
                f"READ:DEV:{self.config.ips.pt1_temperature}:TEMP:SIG:TEMP?",
            ),
            "ips_pt2_temperature": (
                "ips",
                f"READ:DEV:{self.config.ips.pt2_temperature}:TEMP:SIG:TEMP?",
            ),
        }
        readings = {
            name: {
                "command": command,
                "response": self._diagnostic_response(target, command),
            }
            for name, (target, command) in commands.items()
        }
        switch_status = self._read_switch_heater_status()
        readings["derived_switch_heater"] = {
            "status": str(switch_status),
            "target_status": str(self._switch_heater_state(switch_status).target_status),
            "ready": self._switch_heater_state(switch_status).ready,
            "delay_s": self._switch_heater_state(switch_status).delay_s,
            "elapsed_s": self._switch_heater_state(switch_status).elapsed_s,
        }
        return readings

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

    def _diagnostic_response(self, target: str, command: str) -> str:
        try:
            return self._diagnostic_resource(target).query(command)
        except MercuryQueryError as exc:
            return f"ERROR:{exc.original_type}:{exc.original_message}"

    def _read_switch_heater_status(self) -> SwitchHeaterStatus:
        response = self.ips.query(f"READ:DEV:{self.config.ips.magnet_group}:PSU:SIG:SWHT?")
        token = response.split(":")[-1].strip().upper()
        if token.endswith("ON"):
            return SwitchHeaterStatus.ON
        if token.endswith("OFF"):
            return SwitchHeaterStatus.OFF
        return SwitchHeaterStatus.UNKNOWN

    def _read_magnet_action(self) -> MagnetAction:
        response = self._try_read_ips(f"READ:DEV:{self.config.ips.magnet_group}:PSU:ACTN?")
        if response is None:
            return MagnetAction.UNKNOWN
        token = response.split(":")[-1].strip().upper()
        match token:
            case "HOLD":
                return MagnetAction.HOLD
            case "RTOS" | "TO SET" | "TO_SET":
                return MagnetAction.TO_SET
            case "RTOZ" | "TO ZERO" | "TO_ZERO":
                return MagnetAction.TO_ZERO
            case "CLMP" | "CLAMP":
                return MagnetAction.CLAMP
            case _:
                return MagnetAction.UNKNOWN

    def _read_temperature_pid(self, mercury_loop: str) -> PIDState:
        auto = self._try_read_itc_bool(f"READ:DEV:{mercury_loop}:TEMP:LOOP:PIDT?")
        return PIDState(
            mode="AUTO" if auto else "USER" if auto is False else "UNKNOWN",
            p=self._try_read_itc_float(f"READ:DEV:{mercury_loop}:TEMP:LOOP:P?"),
            i=self._try_read_itc_float(f"READ:DEV:{mercury_loop}:TEMP:LOOP:I?"),
            d=self._try_read_itc_float(f"READ:DEV:{mercury_loop}:TEMP:LOOP:D?"),
        )

    def _set_temperature_loop_target(
        self,
        mercury_loop: str,
        target_K: float,
        *,
        rate_K_per_min: float | None = None,
        ramp: bool = False,
    ) -> None:
        if ramp:
            if rate_K_per_min is None:
                raise ValueError("rate_K_per_min is required for a temperature ramp")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RENA:ON")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RSET:{rate_K_per_min:.9g}")
        else:
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RENA:OFF")
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:TSET:{target_K:.9g}")
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:ENAB:ON")

    def _hold_temperature_loop(self, mercury_loop: str, measured_K: float) -> None:
        self._set_temperature_loop_target(mercury_loop, measured_K, ramp=False)

    def _set_temperature_loop_fixed_heater(
        self,
        mercury_loop: str,
        heater_percent: float,
    ) -> None:
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:RENA:OFF")
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:ENAB:OFF")
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:HSET:{heater_percent:.9g}")

    def _set_temperature_loop_pid(
        self,
        mercury_loop: str,
        p: float,
        i: float,
        d: float,
        *,
        auto: bool = False,
    ) -> None:
        pid_table = "ON" if auto else "OFF"
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:PIDT:{pid_table}")
        if not auto:
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:P:{p:.9g}")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:I:{i:.9g}")
            self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:D:{d:.9g}")
        self.itc.set(f"SET:DEV:{mercury_loop}:TEMP:LOOP:ENAB:ON")

    def _switch_heater_state(self, status: SwitchHeaterStatus) -> SwitchHeaterState:
        target = (
            self._switch_heater_target
            if self._switch_heater_target != SwitchHeaterStatus.UNKNOWN
            else status
        )
        delay = self._switch_heater_delay_s(target)
        elapsed = (
            unix_time() - self._switch_heater_changed_at
            if self._switch_heater_changed_at is not None
            else None
        )
        ready = status == target and (elapsed is None or elapsed >= delay)
        return SwitchHeaterState(
            status=status,
            target_status=target,
            ready=ready,
            delay_s=delay,
            last_changed_at=self._switch_heater_changed_at,
            elapsed_s=elapsed,
        )

    def _switch_heater_delay_s(self, status: SwitchHeaterStatus) -> float:
        if status == SwitchHeaterStatus.ON:
            return self.config.ips.switch_on_delay_s
        return self.config.ips.switch_off_delay_s

    def _ensure_switch_heater_ready_for_ramp(self) -> None:
        status = self._read_switch_heater_status()
        switch_state = self._switch_heater_state(status)
        if not switch_state.ready:
            raise PermissionError(
                "Magnet ramp blocked while the persistent switch is transitioning; "
                f"wait {switch_state.delay_s:.0f} s after changing the switch heater"
            )

    def _maybe_adjust_field_rate(
        self,
        field_T: float | None,
        field_rate_T_per_min: float | None,
        field_ramping: bool,
    ) -> None:
        if self._field_requested_rate_T_per_min is None:
            self._field_requested_rate_T_per_min = field_rate_T_per_min
        if self._field_requested_rate_T_per_min is None:
            return
        desired_rate_T_per_min = _field_rate_with_low_field_cap(
            field_T,
            self._field_requested_rate_T_per_min,
        )
        if not field_ramping:
            self._field_rate_T_per_min = desired_rate_T_per_min
            return
        current_rate_T_per_min = _first_available(
            field_rate_T_per_min,
            self._field_rate_T_per_min,
        )
        if (
            current_rate_T_per_min is not None
            and abs(current_rate_T_per_min - desired_rate_T_per_min) <= 1e-9
        ):
            self._field_rate_T_per_min = current_rate_T_per_min
            return
        self.ips.set(
            f"SET:DEV:{self.config.ips.magnet_group}:PSU:SIG:RFST:{desired_rate_T_per_min:.9g}"
        )
        self._field_rate_T_per_min = desired_rate_T_per_min

    def _read_itc_float(self, command: str) -> float | None:
        return _extract_float(self.itc.query(command))

    def _try_read_itc_float(self, command: str) -> float | None:
        try:
            return self._read_itc_float(command)
        except MercuryQueryError:
            return None

    def _read_itc_bool(self, command: str) -> bool:
        return _extract_bool(self.itc.query(command))

    def _try_read_itc_bool(self, command: str) -> bool | None:
        try:
            return self._read_itc_bool(command)
        except MercuryQueryError:
            return None

    def _read_ips_float(self, command: str) -> float | None:
        return _extract_float(self.ips.query(command))

    def _try_read_ips(self, command: str) -> str | None:
        try:
            return self.ips.query(command)
        except MercuryQueryError:
            return None


class HelioxCryostatBackend(MercuryCryostatBackend):
    BACKEND_NAME = "heliox"
    HELIOX_SORB_SENSOR_UID = "MB1.T1"
    HELIOX_SORB_HEATER_UID = "MB0.H1"
    HELIOX_POT_HIGH_SENSOR_UID = "DB7.T1"
    HELIOX_POT_HEATER_UID = "DB2.H1"
    HELIOX_VTI_NEEDLE_UID = "DB4.G1"
    HELIOX_TEMPLATE_THRESHOLDS = {
        "accept_base_K": 0.2950,
        "control_mode_crossover_K": 1.6000,
        "condensed_temp_K": 1.5900,
        "he3_pot_boiloff_K": 5.0000,
        "he3_sorb_cold_K": 1.8000,
        "he3_sorb_high_temp_control_K": 15.0000,
        "he3_sorb_regen_K": 35.0000,
        "he3_sorb_outgas_K": 50.0000,
        "he4_sorb_rapid_cool_K": 18.0000,
        "needle_valve_close_mbar": 0.1000,
        "needle_valve_high_temp_mbar": 10.0000,
        "needle_valve_low_temp_mbar": 5.0000,
        "needle_valve_recondense_mbar": 6.0000,
        "pot_empty_K": 2.0000,
        "rapid_cool_delta_K": 15.0000,
        "rapid_cool_end_K": 13.0000,
        "rapid_cool_exit_K": 5.0000,
        "regen_above_K": 1.5000,
        "target_tolerance_K": 0.005,
    }

    def __init__(self, config: CryostatServiceConfig):
        super().__init__(config)
        self._sample_requested_rate_K_per_min: float | None = None

    def _sample_control_component(self) -> _HelioxSampleControlStrategy:
        component = getattr(self, "_sample_control_cache", None)
        if component is None:
            component = _HelioxSampleControlStrategy(self)
            self._sample_control_cache = component
        return component

    def _temperature_targets(self, loop: str, target_K: float) -> dict[str, float]:
        self._ensure_supported_temperature_loop(loop)
        return super()._temperature_targets(loop, target_K)

    def set_temperature_fixed_heater(self, loop: str, heater_percent: float) -> None:
        if loop != "vti":
            raise PermissionError(
                "Fixed heater control is available only for the VTI on the Heliox backend"
            )
        self._vti_control_component().set_fixed_heater(heater_percent)
        self._mode = CryostatMode.HOLDING

    def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> None:
        if loop != "vti":
            raise PermissionError(
                "Direct PID tuning is available only for the VTI on the Heliox backend"
            )
        self._vti_control_component().set_pid(p, i, d, auto=auto)

    def diagnostics(self) -> dict:
        data = super().diagnostics()
        data["backend"] = "heliox"
        data["abstract_device"] = "HelioxX:HEL"
        data["control_model"] = {
            "sample_temperature_via_abstract_setpoint": True,
            "vti_temperature_via_raw_mercury": True,
            "field_control_via_ips": True,
            "vti_gas_control_via_raw_mercury": True,
            "low_temperature_actuator": "He3 sorb heater",
            "high_temperature_actuator": "He3 pot heater",
        }
        data["heliox_template_devices"] = {
            "sorb_sensor": self.HELIOX_SORB_SENSOR_UID,
            "sorb_heater": self.HELIOX_SORB_HEATER_UID,
            "plate_sensor": "DB6.T1",
            "pot_high_sensor": self.HELIOX_POT_HIGH_SENSOR_UID,
            "pot_low_sensor": self.config.itc.probe_signal,
            "plate_heater": "DB1.H1",
            "pot_heater": self.HELIOX_POT_HEATER_UID,
            "vti_temperature_sensor": self.config.itc.vti_signal,
            "vti_pressure": self.config.itc.pressure,
            "vti_needle_valve": self.HELIOX_VTI_NEEDLE_UID,
        }
        data["heliox_raw_mapping"] = {
            "sample_low_sensor": self.config.itc.probe_signal,
            "sample_low_loop": self.config.itc.probe_loop,
            "sample_high_sensor": self.HELIOX_POT_HIGH_SENSOR_UID,
            "vti_sensor": self.config.itc.vti_signal,
            "vti_loop": self.config.itc.vti_loop,
            "pressure_sensor": self.config.itc.pressure,
            "needle_valve": self.HELIOX_VTI_NEEDLE_UID,
        }
        data["heliox_template_thresholds"] = dict(self.HELIOX_TEMPLATE_THRESHOLDS)
        return data

    def catalog(self) -> dict:
        data = super().catalog()
        data["heliox_status"] = self.itc.query("READ:DEV:HelioxX:HEL:SIG:STAT")
        return data

    def raw_readings(self) -> dict:
        readings = super().raw_readings()
        commands = {
            "heliox_temperature": "READ:DEV:HelioxX:HEL:SIG:TEMP",
            "heliox_status": "READ:DEV:HelioxX:HEL:SIG:STAT",
            "heliox_setpoint": "READ:DEV:HelioxX:HEL:SIG:TSET",
            "heliox_pot_stable": "READ:DEV:HelioxX:HEL:SIG:H3PS",
            "heliox_pot_temperature": "READ:DEV:HelioxX:HEL:SIG:H3PT",
            "heliox_pot_heater_percent": "READ:DEV:HelioxX:HEL:SIG:H3PH",
            "heliox_sorb_stable": "READ:DEV:HelioxX:HEL:SIG:SRBS",
            "heliox_sorb_temperature": "READ:DEV:HelioxX:HEL:SIG:SRBT",
            "heliox_sorb_heater_percent": "READ:DEV:HelioxX:HEL:SIG:SRBH",
            "heliox_raw_sorb_temperature": (
                f"READ:DEV:{self.HELIOX_SORB_SENSOR_UID}:TEMP:SIG:TEMP?"
            ),
            "heliox_raw_pot_high_temperature": (
                f"READ:DEV:{self.HELIOX_POT_HIGH_SENSOR_UID}:TEMP:SIG:TEMP?"
            ),
            "heliox_raw_pot_low_temperature": (
                f"READ:DEV:{self.config.itc.probe_signal}:TEMP:SIG:TEMP?"
            ),
            "heliox_raw_vti_temperature": (f"READ:DEV:{self.config.itc.vti_signal}:TEMP:SIG:TEMP?"),
        }
        readings.update(
            {
                name: {
                    "command": command,
                    "response": self._diagnostic_response("itc", command),
                }
                for name, command in commands.items()
            }
        )
        return readings

    def _ensure_supported_temperature_loop(self, loop: str) -> None:
        if loop not in {"sample", "vti", "both"}:
            raise PermissionError("Heliox backend supports sample, VTI, or both temperature loops")

    def _read_heliox_float(self, command: str) -> float | None:
        return _extract_float(self.itc.query(command))

    def _try_read_heliox_float(self, command: str) -> float | None:
        try:
            return self._read_heliox_float(command)
        except MercuryQueryError:
            return None

    def _read_heliox_status(self) -> str | None:
        response = self.itc.query("READ:DEV:HelioxX:HEL:SIG:STAT")
        token = response.rsplit(":", 1)[-1].strip()
        return token or None

    def _try_read_heliox_status(self) -> str | None:
        try:
            return self._read_heliox_status()
        except MercuryQueryError:
            return None

    def _read_heliox_bool(self, command: str) -> bool | None:
        try:
            response = self.itc.query(command)
        except MercuryQueryError:
            return None
        token = response.rsplit(":", 1)[-1].strip().upper()
        if token == "ON":
            return True
        if token == "OFF":
            return False
        return None

    def _heliox_control_channel(
        self,
        status: str | None,
        *,
        sorb_heater_percent: float | None = None,
        pot_heater_percent: float | None = None,
    ) -> str:
        if (
            pot_heater_percent is not None
            and sorb_heater_percent is not None
            and abs(pot_heater_percent - sorb_heater_percent) > 0.1
        ):
            return "POT" if pot_heater_percent > sorb_heater_percent else "SORB"
        if pot_heater_percent is not None and pot_heater_percent > 0.1:
            return "POT"
        if sorb_heater_percent is not None and sorb_heater_percent > 0.1:
            return "SORB"
        if not status:
            return "SORB"
        normalized = status.upper()
        if "HIGH" in normalized:
            return "POT"
        return "SORB"

    def _heliox_heater_mode_label(self, status: str | None, channel: str) -> str:
        prefix = "HE3_POT_HEATER" if channel == "POT" else "HE3_SORB_HEATER"
        if not status:
            return prefix
        return f"{prefix}:{status}"


def _extract_float(response: str) -> float | None:
    value_token = response.rsplit(":", 1)[-1]
    matches = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value_token)
    if not matches:
        return None
    return float(matches[-1])


def _extract_bool(response: str) -> bool:
    token = response.rsplit(":", 1)[-1].strip().upper()
    return token == "ON"


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


def _within_tolerance(
    value: float | None,
    target: float | None,
    tolerance: float,
) -> bool | None:
    if value is None or target is None:
        return None
    return abs(value - target) <= tolerance


def _inside_tolerance(value: float | None, target: float, tolerance: float) -> bool | None:
    if value is None:
        return None
    return abs(value - target) <= tolerance


def _pressure_mode_from_loop_state(
    loop_enabled: bool | None,
    target_mbar: float | None,
    needle_percent: float | None,
) -> GasControlMode:
    if loop_enabled is True:
        return GasControlMode.PRESSURE_CONTROL
    if loop_enabled is False:
        return GasControlMode.FIXED_NEEDLE
    if target_mbar is not None:
        return GasControlMode.PRESSURE_CONTROL
    if needle_percent is not None:
        return GasControlMode.FIXED_NEEDLE
    return GasControlMode.UNKNOWN


def _field_rate_with_low_field_cap(
    field_T: float | None,
    requested_rate_T_per_min: float,
) -> float:
    if abs(field_T or 0.0) <= LOW_FIELD_RATE_WINDOW_T:
        return min(requested_rate_T_per_min, LOW_FIELD_RATE_LIMIT_T_PER_MIN)
    return requested_rate_T_per_min


def _temperature_targets_for_loop(
    loop: str,
    sample_target_K: float,
    *,
    sample_loop: str,
    vti_loop: str,
) -> dict[str, float]:
    match loop:
        case "sample":
            return {sample_loop: sample_target_K}
        case "vti":
            return {vti_loop: sample_target_K}
        case "both":
            return {
                sample_loop: sample_target_K,
                vti_loop: sample_target_K * 0.9,
            }
        case _:
            raise ValueError("Temperature loop must be 'sample', 'vti', or 'both'")


def _temperature_heater_mode(
    loop_enabled: bool | None,
    ramp_enabled: bool | None,
) -> str:
    if loop_enabled is False:
        return "OFF"
    if ramp_enabled is True:
        return "RAMP"
    if loop_enabled is True:
        return "PID_OR_FIXED_TARGET"
    return "UNKNOWN"


def create_backend(config: CryostatServiceConfig) -> CryostatBackend:
    if config.backend == "mock":
        return MockCryostatBackend(config)
    if config.backend == "mercury":
        return MercuryCryostatBackend(config)
    if config.backend == "heliox":
        return HelioxCryostatBackend(config)
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
        logger.warning("Failed to list VISA resources: %s", exc)
        return {"ok": False, "error": str(exc)}
