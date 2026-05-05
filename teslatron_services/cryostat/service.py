from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from time import monotonic
from typing import Any

from .backends import CryostatBackend, create_backend, list_visa_resources
from .config import CryostatServiceConfig
from .state import CryostatMode, CryostatState, SafetyState


class CryostatService:
    def __init__(
        self,
        config: CryostatServiceConfig,
        backend: CryostatBackend | None = None,
    ):
        self.config = config
        self.backend = backend or create_backend(config)
        self._state = self._read_state_safely()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_log = 0.0

    @property
    def state(self) -> CryostatState:
        return self._state

    async def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run(), name="cryostat-service")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        await self._publish_to(queue, self._state.to_dict())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def ramp_temperature(
        self,
        target_K: float,
        rate_K_per_min: float,
        loop: str = "both",
    ) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_temperature_loop(loop)
        self._validate_temperature(target_K, rate_K_per_min)
        self.backend.ramp_temperature(target_K, rate_K_per_min, loop=loop)
        await self.poll_once()
        return self._state.to_dict()

    async def ramp_field(self, target_T: float, rate_T_per_min: float) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_field(target_T, rate_T_per_min)
        self.backend.ramp_field(target_T, rate_T_per_min)
        await self.poll_once()
        return self._state.to_dict()

    async def hold(self) -> dict[str, Any]:
        self._ensure_writable()
        self.backend.hold()
        await self.poll_once()
        return self._state.to_dict()

    async def abort(self) -> dict[str, Any]:
        self._ensure_writable()
        self.backend.abort()
        await self.poll_once()
        return self._state.to_dict()

    async def set_vti_needle(self, needle_valve_percent: float) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_needle_valve(needle_valve_percent)
        self.backend.set_vti_needle(needle_valve_percent)
        await self.poll_once()
        return self._state.to_dict()

    async def set_vti_pressure(self, pressure_mbar: float) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_pressure(pressure_mbar)
        self.backend.set_vti_pressure(pressure_mbar)
        await self.poll_once()
        return self._state.to_dict()

    async def poll_once(self) -> CryostatState:
        self._state = self._read_state_safely()
        data = self._state.to_dict()
        await self._publish(data)

        now = monotonic()
        if now - self._last_log >= self.config.log_interval_s:
            self._append_log(data)
            self._last_log = now
        return self._state

    def _read_state_safely(self) -> CryostatState:
        try:
            return self.backend.read_state()
        except Exception as exc:
            return CryostatState(
                mode=CryostatMode.ERROR,
                safety=SafetyState(
                    level="error",
                    message=str(exc),
                    safe_to_measure=False,
                    safe_to_change_gate=False,
                    safe_to_change_current=False,
                ),
                backend=self.config.backend,
                error=str(exc),
            )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.poll_interval_s,
                )
            except TimeoutError:
                pass

    async def _publish(self, data: dict[str, Any]) -> None:
        stale = []
        for queue in self._subscribers:
            try:
                await self._publish_to(queue, data)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    async def _publish_to(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        data: dict[str, Any],
    ) -> None:
        if queue.full():
            queue.get_nowait()
        await queue.put(data)

    def _append_log(self, data: dict[str, Any]) -> None:
        path = Path(self.config.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        row = _flatten_state(data)
        with path.open("a", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(row.keys()))
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    def _validate_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        safety = self.config.safety
        if not safety.min_temperature_K <= target_K <= safety.max_temperature_K:
            raise ValueError(f"Temperature target out of range: {target_K} K")
        if rate_K_per_min <= 0 or rate_K_per_min > safety.max_temperature_rate_K_per_min:
            raise ValueError(f"Temperature rate out of range: {rate_K_per_min} K/min")

    def _validate_temperature_loop(self, loop: str) -> None:
        if loop not in {"sample", "vti", "both"}:
            raise ValueError("Temperature loop must be 'sample', 'vti', or 'both'")

    def _validate_field(self, target_T: float, rate_T_per_min: float) -> None:
        safety = self.config.safety
        if abs(target_T) > safety.max_field_T:
            raise ValueError(f"Field target out of range: {target_T} T")
        if rate_T_per_min <= 0 or rate_T_per_min > safety.max_field_rate_T_per_min:
            raise ValueError(f"Field rate out of range: {rate_T_per_min} T/min")

    def _validate_needle_valve(self, needle_valve_percent: float) -> None:
        if not 0.0 <= needle_valve_percent <= 100.0:
            raise ValueError("Needle valve opening must be between 0 and 100 percent")

    def _validate_pressure(self, pressure_mbar: float) -> None:
        if pressure_mbar < 0:
            raise ValueError("Pressure target must be non-negative")

    def _ensure_writable(self) -> None:
        if self.config.read_only:
            raise PermissionError("Cryostat service is running in read-only mode")

    def config_snapshot(self) -> dict[str, Any]:
        return self.config.to_dict()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "service": {
                "backend": self.config.backend,
                "read_only": self.config.read_only,
                "poll_interval_s": self.config.poll_interval_s,
                "log_interval_s": self.config.log_interval_s,
            },
            "backend": self.backend.diagnostics(),
        }

    def visa_resources(self) -> dict[str, Any]:
        return list_visa_resources()

    def catalog(self) -> dict[str, Any]:
        return self.backend.catalog()

    def raw_readings(self) -> dict[str, Any]:
        return self.backend.raw_readings()


def _flatten_state(data: dict[str, Any]) -> dict[str, Any]:
    sample = data["temperature"]["sample"]
    vti = data["temperature"]["vti"]
    return {
        "timestamp": data["timestamp"],
        "mode": data["mode"],
        "backend": data["backend"],
        "sample_temperature_K": sample["temperature_K"],
        "sample_target_K": sample["target_K"],
        "sample_rate_K_per_min": sample["rate_K_per_min"],
        "sample_ramp_end_K": sample["ramp_end_K"],
        "sample_heater_percent": sample["heater_percent"],
        "sample_heater_power_W": sample["heater_power_W"],
        "sample_heater_voltage_V": sample["heater_voltage_V"],
        "sample_mode": sample["mode"],
        "sample_stable": sample["stable"],
        "sample_ramping": sample["ramping"],
        "vti_temperature_K": vti["temperature_K"],
        "vti_target_K": vti["target_K"],
        "vti_rate_K_per_min": vti["rate_K_per_min"],
        "vti_ramp_end_K": vti["ramp_end_K"],
        "vti_heater_percent": vti["heater_percent"],
        "vti_heater_power_W": vti["heater_power_W"],
        "vti_heater_voltage_V": vti["heater_voltage_V"],
        "vti_mode": vti["mode"],
        "vti_stable": vti["stable"],
        "vti_ramping": vti["ramping"],
        "B_T": data["field"]["B_T"],
        "field_target_T": data["field"]["target_T"],
        "field_rate_T_per_min": data["field"]["rate_T_per_min"],
        "field_stable": data["field"]["stable"],
        "field_ramping": data["field"]["ramping"],
        "pressure_mbar": data["pressure"]["mbar"],
        "pressure_target_mbar": data["pressure"]["target_mbar"],
        "needle_valve_percent": data["pressure"]["needle_valve_percent"],
        "pressure_mode": data["pressure"]["mode"],
        "safety_level": data["safety"]["level"],
        "safety_message": data["safety"]["message"],
    }
