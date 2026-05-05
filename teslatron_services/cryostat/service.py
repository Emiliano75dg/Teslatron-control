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

    async def ramp_temperature(self, target_K: float, rate_K_per_min: float) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_temperature(target_K, rate_K_per_min)
        self.backend.ramp_temperature(target_K, rate_K_per_min)
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

    def _validate_field(self, target_T: float, rate_T_per_min: float) -> None:
        safety = self.config.safety
        if abs(target_T) > safety.max_field_T:
            raise ValueError(f"Field target out of range: {target_T} T")
        if rate_T_per_min <= 0 or rate_T_per_min > safety.max_field_rate_T_per_min:
            raise ValueError(f"Field rate out of range: {rate_T_per_min} T/min")

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
    return {
        "timestamp": data["timestamp"],
        "mode": data["mode"],
        "backend": data["backend"],
        "probe_K": data["temperature"]["probe_K"],
        "vti_K": data["temperature"]["vti_K"],
        "temperature_target_K": data["temperature"]["target_K"],
        "temperature_rate_K_per_min": data["temperature"]["rate_K_per_min"],
        "temperature_stable": data["temperature"]["stable"],
        "temperature_ramping": data["temperature"]["ramping"],
        "B_T": data["field"]["B_T"],
        "field_target_T": data["field"]["target_T"],
        "field_rate_T_per_min": data["field"]["rate_T_per_min"],
        "field_stable": data["field"]["stable"],
        "field_ramping": data["field"]["ramping"],
        "pressure_mbar": data["pressure"]["mbar"],
        "needle_valve_percent": data["pressure"]["needle_valve_percent"],
        "safety_level": data["safety"]["level"],
        "safety_message": data["safety"]["message"],
    }
