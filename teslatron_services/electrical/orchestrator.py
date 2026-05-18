from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from time import monotonic, time
from time import sleep as blocking_sleep
from typing import Any, Awaitable, Callable
from urllib.request import Request, urlopen

from .config import ElectricalServiceConfig, InstrumentConfig, MeasurementPlanConfig
from .drivers.base import ElectricalInstrumentDriver
from .drivers.mock import MockElectricalDriver
from .persistence import ElectricalCsvMeasurementWriter, JsonlMeasurementWriter, flatten_measurement
from .state import CryostatCacheState, ElectricalServiceState, MeasurementRunState
from .vdp import run_vdp_characterization_for_teslatron

logger = logging.getLogger(__name__)

CryostatFetcher = Callable[[], Awaitable[dict[str, Any]]]
RecipeNotifier = Callable[[str, str | None], Awaitable[dict[str, Any]]]


class ElectricalMeasurementService:
    def __init__(
        self,
        config: ElectricalServiceConfig,
        *,
        cryostat_fetcher: CryostatFetcher | None = None,
        recipe_notifier: RecipeNotifier | None = None,
        instruments: dict[str, ElectricalInstrumentDriver] | None = None,
    ):
        self.config = config
        self._cryostat_fetcher = cryostat_fetcher or self._default_cryostat_fetcher
        self._recipe_notifier = recipe_notifier or self._default_recipe_notifier
        self._writer = JsonlMeasurementWriter(config.measurement_session.save_dir)
        self._csv_writer = ElectricalCsvMeasurementWriter(config.measurement_session.save_dir)
        self._state = ElectricalServiceState()
        self._stop_event = asyncio.Event()
        self._run_stop_event = asyncio.Event()
        self._resource_lock = asyncio.Lock()
        self._poll_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="electrical-service")
        self._instruments = instruments or {
            name: _build_driver(instrument_config)
            for name, instrument_config in config.instruments.items()
        }

    async def start(self) -> None:
        self._stop_event.clear()
        self._run_stop_event.clear()
        if getattr(self._executor, "_shutdown", False):
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="electrical-service"
            )
        for instrument in self._instruments.values():
            instrument.connect()
        await self.refresh_cryostat_state()
        self._poll_task = asyncio.create_task(self._poll_cryostat_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        await self.stop_run()
        if self._poll_task is not None:
            await self._poll_task
            self._poll_task = None
        for instrument in self._instruments.values():
            instrument.shutdown()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def config_snapshot(self) -> dict[str, Any]:
        payload = self.config.to_dict()
        payload["instruments"] = {
            name: {
                "driver": item.driver,
                "address": item.address,
            }
            for name, item in self.config.instruments.items()
        }
        payload["plans"] = {
            name: {
                "id": plan.id,
                "mode": plan.mode,
                "trigger": {
                    "type": plan.trigger.type,
                    "signal": plan.trigger.signal,
                },
                "steps": [
                    {
                        "instrument": step.instrument,
                        "action": step.action,
                    }
                    for step in plan.steps
                ],
                "require_safe_to_measure": plan.require_safe_to_measure,
                "completion": {
                    "notify_recipe": plan.completion.notify_recipe,
                    "success_signal": plan.completion.success_signal,
                    "failure_signal": plan.completion.failure_signal,
                },
            }
            for name, plan in self.config.plans.items()
        }
        return payload

    def state_snapshot(self) -> dict[str, Any]:
        self._state.timestamp = time()
        return self._state.to_dict()

    def run_status(self) -> dict[str, Any]:
        return self.state_snapshot()["run"]

    def list_plans(self) -> list[dict[str, Any]]:
        return list(self.config_snapshot()["plans"].values())

    async def refresh_cryostat_state(self) -> dict[str, Any]:
        try:
            snapshot = await self._cryostat_fetcher()
            self._state.cryostat = CryostatCacheState(
                connected=True,
                last_fetch_at=time(),
                last_error=None,
                snapshot=snapshot,
            )
        except Exception as exc:
            logger.warning("Failed to refresh cryostat state: %s", exc)
            self._state.cryostat.connected = False
            self._state.cryostat.last_error = str(exc)
            self._state.cryostat.last_fetch_at = time()
        return self._state.cryostat.snapshot

    async def start_periodic_run(
        self,
        *,
        run_id: str,
        instrument: str,
        interval_s: float,
        max_points: int | None = None,
        plan_id: str = "periodic",
        require_safe_to_measure: bool = True,
    ) -> dict[str, Any]:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        if instrument not in self._instruments:
            raise ValueError(f"Unknown instrument: {instrument}")
        if self._run_task is not None and not self._run_task.done():
            raise ValueError("A measurement run is already active")

        self._run_stop_event = asyncio.Event()
        csv_path = self._csv_writer.begin_run(run_id)
        self._state.run = MeasurementRunState(
            status="running",
            run_id=run_id,
            instrument=instrument,
            plan_id=plan_id,
            interval_s=interval_s,
            max_points=max_points,
            started_at=time(),
            run_start_monotonic_s=monotonic(),
            output_path=str(self._writer.run_path(run_id)),
            electrical_csv_path=str(csv_path),
            output_paths={
                "jsonl": str(self._writer.run_path(run_id)),
                "electrical_csv": str(csv_path),
            },
        )
        self._run_task = asyncio.create_task(
            self._periodic_run_loop(
                require_safe_to_measure=require_safe_to_measure,
            )
        )
        return self.run_status()

    async def trigger_recipe_signal(
        self,
        signal: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        plan = self._plan_for_recipe_signal(signal)
        if plan is None:
            raise ValueError(f"No electrical plan is configured for recipe signal {signal!r}")
        if self._run_task is not None and not self._run_task.done():
            raise ValueError("A measurement run is already active")
        run_id = _recipe_run_id(plan.id, signal)
        primary_instrument = plan.steps[0].instrument
        self._run_stop_event = asyncio.Event()
        csv_path = self._csv_writer.begin_run(run_id)
        self._state.run = MeasurementRunState(
            status="running",
            run_id=run_id,
            instrument=primary_instrument,
            plan_id=plan.id,
            trigger_signal=signal,
            started_at=time(),
            run_start_monotonic_s=monotonic(),
            output_path=str(self._writer.run_path(run_id)),
            electrical_csv_path=str(csv_path),
            output_paths={
                "jsonl": str(self._writer.run_path(run_id)),
                "electrical_csv": str(csv_path),
            },
        )
        self._run_task = asyncio.create_task(
            self._execute_recipe_plan(plan, signal, message),
            name=f"electrical-plan-{plan.id}",
        )
        return self.run_status()

    async def stop_run(self) -> dict[str, Any]:
        self._run_stop_event.set()
        if self._run_task is not None:
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        if self._state.run.status == "running":
            self._state.run.status = "stopped"
            self._state.run.stopped_at = time()
        return self.run_status()

    async def _poll_cryostat_loop(self) -> None:
        interval_s = max(0.2, self.config.cryostat.poll_interval_s)
        while not self._stop_event.is_set():
            await self.refresh_cryostat_state()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_s)
            except TimeoutError:
                pass

    async def _periodic_run_loop(self, *, require_safe_to_measure: bool) -> None:
        run = self._state.run
        assert run.run_id is not None
        assert run.instrument is not None
        assert run.interval_s is not None
        try:
            while not self._run_stop_event.is_set():
                await self.refresh_cryostat_state()
                if require_safe_to_measure and not self._safe_to_measure():
                    await self._run_blocking(blocking_sleep, min(run.interval_s, 0.5))
                    continue
                event = await self._acquire_measurement(
                    run.instrument, run.run_id, run.plan_id or "periodic"
                )
                run.points_acquired += 1
                run.last_event = event
                if run.max_points is not None and run.points_acquired >= run.max_points:
                    run.status = "completed"
                    run.stopped_at = time()
                    self._run_stop_event.set()
                    break
                if run.max_points is None:
                    await self._run_blocking(blocking_sleep, run.interval_s)
            if run.status == "running":
                run.status = "stopped"
                run.stopped_at = time()
        except Exception as exc:
            logger.exception("Electrical periodic run failed")
            run.status = "error"
            run.error = str(exc)
            run.stopped_at = time()

    async def _execute_recipe_plan(
        self,
        plan: MeasurementPlanConfig,
        signal: str,
        message: str | None,
    ) -> None:
        run = self._state.run
        try:
            await self.refresh_cryostat_state()
            if plan.require_safe_to_measure and not self._safe_to_measure():
                raise RuntimeError("Cryostat is not currently safe to measure")
            last_event = None
            for step in plan.steps:
                if self._run_stop_event.is_set():
                    raise asyncio.CancelledError
                if step.action == "measure":
                    last_event = await self._acquire_measurement(
                        step.instrument,
                        run.run_id or _recipe_run_id(plan.id, signal),
                        plan.id,
                    )
                elif step.action == "vdp_characterization":
                    last_event = await self._run_vdp_characterization(
                        run_id=run.run_id or _recipe_run_id(plan.id, signal),
                    )
                    if last_event.get("status") == "stopped":
                        raise asyncio.CancelledError
                else:
                    raise ValueError(f"Unsupported electrical action: {step.action}")
                run.instrument = step.instrument
                run.points_acquired += _result_point_count(last_event)
                run.last_event = last_event
                if last_event.get("csv_path"):
                    run.output_path = str(last_event["csv_path"])
                    run.output_paths["csv"] = str(last_event["csv_path"])
            run.status = "completed"
            run.stopped_at = time()
            await self._notify_plan_completion(plan, signal, "completed", message)
        except asyncio.CancelledError:
            run.status = "aborted"
            run.stopped_at = time()
            await self._notify_plan_completion(plan, signal, "aborted", message)
            raise
        except Exception as exc:
            logger.exception("Electrical recipe plan failed")
            run.status = "error"
            run.error = str(exc)
            run.stopped_at = time()
            await self._notify_plan_completion(plan, signal, "failed", str(exc))

    async def _acquire_measurement(
        self,
        instrument_name: str,
        run_id: str,
        plan_id: str,
    ) -> dict[str, Any]:
        async with self._resource_lock:
            driver = self._instruments[instrument_name]
            payload = await self._run_blocking(driver.measure)
            timestamp_unix_s = time()
            cryostat = self._cryostat_summary()
            event = {
                "timestamp": timestamp_unix_s,
                "run_id": run_id,
                "plan_id": plan_id,
                "instrument": instrument_name,
                "measurement": payload,
                "cryostat": cryostat,
            }
            jsonl_path = await self._run_blocking(self._writer.append_event, run_id, event)
            csv_row = self._build_csv_row(
                run_id=run_id,
                plan_id=plan_id,
                instrument_name=instrument_name,
                timestamp_unix_s=timestamp_unix_s,
                measurement=payload,
                cryostat=cryostat,
            )
            csv_path = await self._run_blocking(self._csv_writer.append_row, run_id, csv_row)
            event["jsonl_path"] = str(jsonl_path)
            event["electrical_csv_path"] = str(csv_path)
            self._state.run.output_path = str(jsonl_path)
            self._state.run.electrical_csv_path = str(csv_path)
            self._state.run.output_paths["jsonl"] = str(jsonl_path)
            self._state.run.output_paths["electrical_csv"] = str(csv_path)
            return event

    async def _run_vdp_characterization(self, *, run_id: str) -> dict[str, Any]:
        output_dir = self._writer.run_path(run_id).parent
        async with self._resource_lock:
            return await self._run_blocking(
                run_vdp_characterization_for_teslatron,
                config=self.config.vdp,
                run_id=run_id,
                output_dir=output_dir,
                cryostat_snapshot_getter=self._cryostat_snapshot_sync,
                stop_requested=self._run_stop_event.is_set,
            )

    def _cryostat_summary(self) -> dict[str, Any]:
        snapshot = self._state.cryostat.snapshot or {}
        temperature = snapshot.get("temperature", {}) if isinstance(snapshot, dict) else {}
        field = snapshot.get("field", {}) if isinstance(snapshot, dict) else {}
        pressure = snapshot.get("pressure", {}) if isinstance(snapshot, dict) else {}
        safety = snapshot.get("safety", {}) if isinstance(snapshot, dict) else {}
        return {
            "timestamp": snapshot.get("timestamp") if isinstance(snapshot, dict) else None,
            "sample_temperature_K": temperature.get("sample", {}).get("temperature_K")
            if isinstance(temperature.get("sample", {}), dict)
            else None,
            "vti_temperature_K": temperature.get("vti", {}).get("temperature_K")
            if isinstance(temperature.get("vti", {}), dict)
            else None,
            "field_T": field.get("B_T"),
            "pressure_mbar": pressure.get("mbar"),
            "safe_to_measure": safety.get("safe_to_measure", False),
        }

    def _build_csv_row(
        self,
        *,
        run_id: str,
        plan_id: str,
        instrument_name: str,
        timestamp_unix_s: float,
        measurement: dict[str, Any],
        cryostat: dict[str, Any],
    ) -> dict[str, Any]:
        started_monotonic = self._state.run.run_start_monotonic_s
        time_relative_s = 0.0 if started_monotonic is None else monotonic() - started_monotonic
        row: dict[str, Any] = {
            "run_id": run_id,
            "plan_id": plan_id,
            "instrument": instrument_name,
            "timestamp_unix_s": timestamp_unix_s,
            "timestamp_iso": _timestamp_iso_utc(timestamp_unix_s),
            "time_relative_s": time_relative_s,
            "sample_temperature_K": cryostat.get("sample_temperature_K"),
            "field_T": cryostat.get("field_T"),
            "safe_to_measure": cryostat.get("safe_to_measure"),
            "vti_temperature_K": cryostat.get("vti_temperature_K"),
            "pressure_mbar": cryostat.get("pressure_mbar"),
            "cryostat_timestamp": cryostat.get("timestamp"),
        }
        for key, value in flatten_measurement(measurement).items():
            target_key = key if key not in row else f"measurement_{key}"
            row[target_key] = value
        return row

    async def _run_blocking(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: func(*args, **kwargs),
        )

    def _safe_to_measure(self) -> bool:
        if not self._state.cryostat.connected:
            return False
        last_fetch_at = self._state.cryostat.last_fetch_at
        if last_fetch_at is None:
            return False
        if time() - last_fetch_at > self.config.cryostat.stale_after_s:
            return False
        safety = self._state.cryostat.snapshot.get("safety", {})
        return bool(safety.get("safe_to_measure", False))

    def _cryostat_snapshot_sync(self) -> dict[str, Any]:
        return dict(self._state.cryostat.snapshot or {})

    def _plan_for_recipe_signal(self, signal: str) -> MeasurementPlanConfig | None:
        for plan in self.config.plans.values():
            if plan.trigger.type == "recipe_signal" and plan.trigger.signal == signal:
                return plan
        return None

    async def _notify_plan_completion(
        self,
        plan: MeasurementPlanConfig,
        signal: str,
        status: str,
        message: str | None,
    ) -> None:
        if not plan.completion.notify_recipe:
            return
        if status == "completed":
            completion_signal = plan.completion.success_signal or f"{signal}.completed"
        else:
            completion_signal = plan.completion.failure_signal or f"{signal}.failed"
        suffix = f"plan={plan.id} status={status}"
        full_message = suffix if not message else f"{message} ({suffix})"
        await self._recipe_notifier(completion_signal, full_message)

    async def _default_cryostat_fetcher(self) -> dict[str, Any]:
        return await self._run_blocking(self._load_cryostat_state_blocking)

    async def _default_recipe_notifier(
        self,
        signal: str,
        message: str | None,
    ) -> dict[str, Any]:
        return await self._run_blocking(self._post_recipe_signal_blocking, signal, message)

    def _load_cryostat_state_blocking(self) -> dict[str, Any]:
        with urlopen(
            self.config.cryostat.state_url,
            timeout=self.config.cryostat.timeout_s,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_recipe_signal_blocking(
        self,
        signal: str,
        message: str | None,
    ) -> dict[str, Any]:
        request = Request(
            self.config.cryostat.recipe_signal_url,
            data=json.dumps({"signal": signal, "message": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.config.cryostat.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))


def _build_driver(config: InstrumentConfig) -> ElectricalInstrumentDriver:
    if config.driver == "mock":
        return MockElectricalDriver(config)
    raise ValueError(f"Unsupported electrical driver: {config.driver}")


def _recipe_run_id(plan_id: str, signal: str) -> str:
    return f"{plan_id}_{signal}_{int(time())}"


def _result_point_count(result: dict[str, Any]) -> int:
    records = result.get("records")
    if isinstance(records, list):
        return len(records)
    return 1


def _timestamp_iso_utc(timestamp_unix_s: float) -> str:
    return (
        datetime.fromtimestamp(timestamp_unix_s, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
