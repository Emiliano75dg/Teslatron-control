from __future__ import annotations

import asyncio
import contextlib
import copy
import csv
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, time
from typing import Any, Callable

from .backends import CryostatBackend, create_backend, list_visa_resources
from .config import CryostatServiceConfig, InsertCapabilitiesConfig
from .state import CryostatMode, CryostatState, SafetyState

logger = logging.getLogger(__name__)


class CryostatService:
    def __init__(
        self,
        config: CryostatServiceConfig,
        backend: CryostatBackend | None = None,
    ):
        self.config = config
        self.backend = backend or create_backend(config)
        self._state = self._read_state_safely()
        self._hardware_lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._recipe_task: asyncio.Task[None] | None = None
        self._recipe_signal_events: dict[str, asyncio.Event] = {}
        self._recipe_status: dict[str, Any] = self._empty_recipe_status()
        self._stop_event = asyncio.Event()
        self._last_log = 0.0

    @property
    def state(self) -> CryostatState:
        return self._state

    def measurement_context(self) -> dict[str, Any]:
        timestamp = self._state.timestamp
        return {
            "timestamp_unix_s": timestamp,
            "timestamp_iso": datetime.fromtimestamp(timestamp, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "sample_temperature_K": self._state.temperature.sample.temperature_K,
            "field_T": self._state.field.B_T,
            "safe_to_measure": self._state.safety.safe_to_measure,
        }

    async def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run(), name="cryostat-service")

    async def stop(self) -> None:
        await self.abort_recipe()
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        async with self._hardware_lock:
            await self._run_blocking(self.backend.close)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        await self._publish_to(queue, self.state_snapshot())
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
        self._ensure_temperature_supported(loop)
        self._validate_temperature(target_K, rate_K_per_min)
        if loop == "both":
            self._validate_temperature(target_K * 0.9, rate_K_per_min)
        return await self._run_hardware_transaction(
            lambda: self.backend.ramp_temperature(target_K, rate_K_per_min, loop=loop)
        )

    async def set_temperature_target(
        self,
        target_K: float,
        loop: str = "both",
    ) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_temperature_loop(loop)
        self._ensure_temperature_supported(loop)
        self._validate_temperature_target(target_K)
        if loop == "both":
            self._validate_temperature_target(target_K * 0.9)
        return await self._run_hardware_transaction(
            lambda: self.backend.set_temperature_target(target_K, loop=loop)
        )

    async def ramp_field(self, target_T: float, rate_T_per_min: float) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_field_supported()
        self._validate_field(target_T, rate_T_per_min)
        return await self._run_hardware_transaction(
            lambda: self.backend.ramp_field(target_T, rate_T_per_min)
        )

    async def ramp_to_zero(self, rate_T_per_min: float) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_field_supported()
        self._validate_field(0.0, rate_T_per_min)
        return await self._run_hardware_transaction(
            lambda: self.backend.ramp_to_zero(rate_T_per_min)
        )

    async def clamp(self) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_field_supported()
        return await self._run_hardware_transaction(self.backend.clamp)

    async def hold(self) -> dict[str, Any]:
        self._ensure_writable()
        return await self._run_hardware_transaction(self.backend.hold)

    async def abort(self) -> dict[str, Any]:
        self._ensure_writable()
        return await self._run_hardware_transaction(self.backend.abort)

    async def set_vti_needle(self, needle_valve_percent: float) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_capability("gas_control", "Gas control is not supported for the active insert")
        self._validate_needle_valve(needle_valve_percent)
        return await self._run_hardware_transaction(
            lambda: self.backend.set_vti_needle(needle_valve_percent)
        )

    async def set_vti_pressure(self, pressure_mbar: float) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_capability("gas_control", "Gas control is not supported for the active insert")
        self._validate_pressure(pressure_mbar)
        return await self._run_hardware_transaction(
            lambda: self.backend.set_vti_pressure(pressure_mbar)
        )

    async def set_temperature_fixed_heater(
        self,
        loop: str,
        heater_percent: float,
    ) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_temperature_loop(loop)
        self._ensure_capability(
            "fixed_heater", "Fixed heater mode is not supported for the active insert"
        )
        self._ensure_temperature_supported(loop)
        self._validate_heater_percent(heater_percent)
        return await self._run_hardware_transaction(
            lambda: self.backend.set_temperature_fixed_heater(loop, heater_percent)
        )

    async def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> dict[str, Any]:
        self._ensure_writable()
        self._validate_temperature_loop(loop)
        self._ensure_capability("pid_control", "PID control is not supported for the active insert")
        self._ensure_temperature_supported(loop)
        self._validate_pid(p, i, d)
        return await self._run_hardware_transaction(
            lambda: self.backend.set_temperature_pid(loop, p, i, d, auto=auto)
        )

    async def set_switch_heater(self, enabled: bool) -> dict[str, Any]:
        self._ensure_writable()
        self._ensure_field_supported()
        return await self._run_hardware_transaction(lambda: self.backend.set_switch_heater(enabled))

    async def apply_sample_sensor(self, preset_id: str) -> dict[str, Any]:
        self._ensure_writable()
        available = self.config.available_sample_sensor_presets()
        if preset_id not in available:
            raise ValueError(f"Unknown sample sensor preset for active insert: {preset_id}")
        sensor = available[preset_id]
        await self._run_hardware_transaction(lambda: self.backend.apply_sample_sensor(sensor))
        self.config.active_sample_sensor = preset_id
        return self.config_snapshot()

    async def poll_once(self) -> CryostatState:
        async with self._hardware_lock:
            self._state = await self._run_blocking(self._read_state_safely)
        data = self.state_snapshot()
        await self._publish_and_log(data)
        return self._state

    def state_snapshot(self) -> dict[str, Any]:
        data = self._state.to_dict()
        data["recipe"] = copy.deepcopy(self._recipe_status)
        return data

    def recipe_status(self) -> dict[str, Any]:
        return copy.deepcopy(self._recipe_status)

    def pending_external_measurement(self) -> dict[str, Any]:
        pending = copy.deepcopy(self._recipe_status.get("external_measurement"))
        if self._recipe_status.get("status") != "waiting_external_measurement" or not pending:
            return {"pending": False}
        return {
            "pending": True,
            **pending,
            "recipe_status": self._recipe_status.get("status"),
            **self.measurement_context(),
        }

    def list_saved_recipes(self) -> list[dict[str, Any]]:
        recipes = []
        for path in sorted(self._recipe_dir().glob("*.json")):
            try:
                recipe = self._load_saved_recipe_file(path)
                recipes.append(self._saved_recipe_summary(path, recipe))
            except (ValueError, json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping unreadable saved recipe at %s: %s", path, exc)
        recipes.sort(key=lambda item: item["name"].lower())
        return recipes

    def load_saved_recipe(self, recipe_id: str) -> dict[str, Any]:
        path = self._recipe_file_path(recipe_id)
        recipe = self._load_saved_recipe_file(path)
        return {
            "id": path.stem,
            "name": recipe["name"],
            "steps": recipe["steps"],
        }

    async def save_recipe(
        self,
        recipe: dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        self._ensure_writable()
        normalized = self._normalized_recipe_definition(recipe)
        path = self._recipe_output_path(normalized["name"])
        if path.exists() and not overwrite:
            raise ValueError(f"A saved recipe named {normalized['name']!r} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=2) + "\n")
        return self._saved_recipe_summary(path, normalized)

    async def delete_saved_recipe(self, recipe_id: str) -> None:
        self._ensure_writable()
        path = self._recipe_file_path(recipe_id)
        path.unlink()

    async def rename_saved_recipe(self, recipe_id: str, new_name: str) -> dict[str, Any]:
        self._ensure_writable()
        source = self._recipe_file_path(recipe_id)
        recipe = self._load_saved_recipe_file(source)
        normalized = self._normalized_recipe_definition(
            {
                "name": new_name,
                "steps": recipe["steps"],
            }
        )
        target = self._recipe_output_path(normalized["name"])
        if target != source and target.exists():
            raise ValueError(f"A saved recipe named {normalized['name']!r} already exists")
        source.rename(target)
        target.write_text(json.dumps(normalized, indent=2) + "\n")
        return self._saved_recipe_summary(target, normalized)

    async def duplicate_saved_recipe(self, recipe_id: str, new_name: str) -> dict[str, Any]:
        self._ensure_writable()
        source = self._recipe_file_path(recipe_id)
        recipe = self._load_saved_recipe_file(source)
        normalized = self._normalized_recipe_definition(
            {
                "name": new_name,
                "steps": recipe["steps"],
            }
        )
        target = self._recipe_output_path(normalized["name"])
        if target.exists():
            raise ValueError(f"A saved recipe named {normalized['name']!r} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(normalized, indent=2) + "\n")
        return self._saved_recipe_summary(target, normalized)

    async def start_recipe(self, recipe: dict[str, Any]) -> dict[str, Any]:
        self._ensure_writable()
        if self._recipe_task is not None and not self._recipe_task.done():
            raise ValueError("A recipe is already running")
        normalized = self._normalized_recipe_definition(recipe)
        steps = normalized["steps"]
        self._recipe_signal_events = {}
        self._recipe_status = {
            "status": "running",
            "name": normalized["name"],
            "steps": steps,
            "current_step_index": None,
            "current_step": None,
            "message": "Recipe started",
            "started_at": time(),
            "finished_at": None,
            "error": None,
            "waiting_signal": None,
            "last_signal": None,
            "external_measurement": None,
        }
        await self._publish(self.state_snapshot())
        self._recipe_task = asyncio.create_task(
            self._run_recipe(steps),
            name="cryostat-recipe",
        )
        return self.recipe_status()

    async def abort_recipe(self) -> dict[str, Any]:
        task = self._recipe_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self.recipe_status()

    async def acknowledge_recipe(self) -> dict[str, Any]:
        if self._recipe_status.get("status") != "waiting_signal":
            raise ValueError("Recipe is not waiting for a signal")
        signal = str(self._recipe_status.get("waiting_signal") or "manual")
        self._set_recipe_signal(signal)
        self._recipe_status["last_signal"] = {
            "signal": signal,
            "message": "Manual confirmation",
            "received_at": time(),
        }
        return self.recipe_status()

    async def signal_recipe(
        self,
        signal: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_recipe_signal(signal)
        self._set_recipe_signal(normalized)
        self._recipe_status["last_signal"] = {
            "signal": normalized,
            "message": message,
            "received_at": time(),
            "metadata": copy.deepcopy(metadata) if metadata is not None else None,
        }
        await self._publish(self.state_snapshot())
        return self.recipe_status()

    async def complete_external_measurement(
        self,
        request_signal: str,
        status: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pending = self._recipe_status.get("external_measurement")
        if self._recipe_status.get("status") != "waiting_external_measurement" or not pending:
            raise ValueError("No external measurement is pending")
        normalized_request = self._normalize_recipe_signal(request_signal)
        if normalized_request != pending["request_signal"]:
            raise ValueError("request_signal does not match the pending external measurement")
        if status == "completed":
            target_signal = pending["completion_signal"]
        elif status == "failed":
            target_signal = pending["failure_signal"]
        else:
            raise ValueError("External measurement status must be 'completed' or 'failed'")
        return await self.signal_recipe(target_signal, message, metadata=metadata)

    async def _run_hardware_transaction(
        self,
        operation: Callable[[], None],
    ) -> dict[str, Any]:
        async with self._hardware_lock:
            await self._run_blocking(operation)
            self._state = await self._run_blocking(self._read_state_safely)
        data = self.state_snapshot()
        await self._publish_and_log(data)
        return data

    async def _run_recipe(self, steps: list[dict[str, Any]]) -> None:
        try:
            for index, step in enumerate(steps):
                self._recipe_status.update(
                    {
                        "status": "running",
                        "current_step_index": index,
                        "current_step": copy.deepcopy(step),
                        "message": self._recipe_step_message(step),
                        "error": None,
                        "waiting_signal": None,
                        "external_measurement": None,
                    }
                )
                await self._publish(self.state_snapshot())
                await self._execute_recipe_step(step)
            self._recipe_status.update(
                {
                    "status": "completed",
                    "current_step_index": None,
                    "current_step": None,
                    "message": "Recipe completed",
                    "finished_at": time(),
                    "waiting_signal": None,
                    "external_measurement": None,
                }
            )
        except asyncio.CancelledError:
            self._recipe_status.update(
                {
                    "status": "aborted",
                    "message": "Recipe aborted",
                    "finished_at": time(),
                    "waiting_signal": None,
                    "external_measurement": None,
                }
            )
            raise
        except Exception as exc:
            logger.exception("Recipe execution failed")
            self._recipe_status.update(
                {
                    "status": "error",
                    "message": str(exc),
                    "error": str(exc),
                    "finished_at": time(),
                    "waiting_signal": None,
                    "external_measurement": None,
                }
            )
        finally:
            await self._publish(self.state_snapshot())

    async def _execute_recipe_step(self, step: dict[str, Any]) -> None:
        step_type = step["type"]
        if step_type == "ramp_temperature":
            await self.ramp_temperature(
                step["target_K"],
                step["rate_K_per_min"],
                loop=step.get("loop", "both"),
            )
            await self._wait_for_temperature_step(step)
        elif step_type == "set_temperature_target":
            await self.set_temperature_target(
                step["target_K"],
                loop=step.get("loop", "both"),
            )
        elif step_type == "ramp_field":
            await self.ramp_field(step["target_T"], step["rate_T_per_min"])
            await self._wait_for_field_step(step["target_T"], step)
        elif step_type == "ramp_to_zero":
            await self.ramp_to_zero(step["rate_T_per_min"])
            await self._wait_for_field_step(0.0, step)
        elif step_type == "wait":
            await asyncio.sleep(step["duration_s"])
        elif step_type == "signal":
            signal = step["signal"]
            self._recipe_status.update(
                {
                    "status": "waiting_signal",
                    "waiting_signal": signal,
                    "message": step.get("message") or f"Waiting for signal {signal}",
                }
            )
            await self._publish(self.state_snapshot())
            event = self._recipe_signal_events.setdefault(signal, asyncio.Event())
            await event.wait()
            event.clear()
        elif step_type == "external_measurement":
            await self._wait_for_external_measurement_step(step)

    async def _wait_for_external_measurement_step(self, step: dict[str, Any]) -> None:
        self._recipe_status.update(
            {
                "status": "waiting_external_measurement",
                "message": step["message"],
                "external_measurement": {
                    "mode": step["mode"],
                    "request_signal": step["request_signal"],
                    "completion_signal": step["completion_signal"],
                    "failure_signal": step["failure_signal"],
                    "message": step["message"],
                    "timeout_s": step["timeout_s"],
                    "requested_at": time(),
                },
            }
        )
        await self._publish(self.state_snapshot())
        matched_signal = await self._wait_for_named_signal(
            [step["completion_signal"], step["failure_signal"]],
            timeout_s=step["timeout_s"],
            label=f"external measurement {step['request_signal']}",
        )
        if matched_signal == step["failure_signal"]:
            detail = self._recipe_status.get("last_signal") or {}
            failure_message = detail.get("message") or step["message"]
            raise RuntimeError(
                f"External measurement failed for {step['request_signal']}: {failure_message}"
            )

    async def _wait_for_named_signal(
        self,
        signals: list[str],
        *,
        timeout_s: float,
        label: str,
    ) -> str:
        events = []
        waiters = []
        try:
            for signal in signals:
                event = self._recipe_signal_events.setdefault(signal, asyncio.Event())
                events.append((signal, event))
                waiters.append(asyncio.create_task(event.wait()))
            done, pending = await asyncio.wait(
                waiters,
                timeout=timeout_s if timeout_s > 0 else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError(f"Timed out waiting for {label}")
            completed_task = next(iter(done))
            completed_index = waiters.index(completed_task)
            matched_signal = events[completed_index][0]
            for _, event in events:
                event.clear()
            return matched_signal
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            for waiter in waiters:
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter

    def _validate_recipe(self, recipe: dict[str, Any]) -> list[dict[str, Any]]:
        raw_steps = recipe.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("Recipe must contain at least one step")
        if len(raw_steps) > 100:
            raise ValueError("Recipe cannot contain more than 100 steps")
        return [self._validate_recipe_step(step) for step in raw_steps]

    def _validate_recipe_step(self, step: Any) -> dict[str, Any]:
        if not isinstance(step, dict):
            raise ValueError("Recipe steps must be objects")
        step_type = step.get("type")
        if step_type == "ramp_temperature":
            loop = str(step.get("loop", "both"))
            target_K = _required_float(step, "target_K")
            rate_K_per_min = _required_float(step, "rate_K_per_min")
            self._validate_temperature_loop(loop)
            self._ensure_temperature_supported(loop)
            self._validate_temperature(target_K, rate_K_per_min)
            if loop == "both":
                self._validate_temperature(target_K * 0.9, rate_K_per_min)
            return {
                "type": step_type,
                "loop": loop,
                "target_K": target_K,
                "rate_K_per_min": rate_K_per_min,
                "tolerance_K": _optional_float(step, "tolerance_K", 0.05),
                "stable_s": _optional_float(step, "stable_s", 0.0),
                "timeout_s": _optional_float(step, "timeout_s", 24 * 60 * 60),
            }
        if step_type == "set_temperature_target":
            loop = str(step.get("loop", "both"))
            target_K = _required_float(step, "target_K")
            self._validate_temperature_loop(loop)
            self._ensure_temperature_supported(loop)
            self._validate_temperature_target(target_K)
            if loop == "both":
                self._validate_temperature_target(target_K * 0.9)
            return {"type": step_type, "loop": loop, "target_K": target_K}
        if step_type == "ramp_field":
            self._ensure_field_supported()
            target_T = _required_float(step, "target_T")
            rate_T_per_min = _required_float(step, "rate_T_per_min")
            self._validate_field(target_T, rate_T_per_min)
            return {
                "type": step_type,
                "target_T": target_T,
                "rate_T_per_min": rate_T_per_min,
                "tolerance_T": _optional_float(step, "tolerance_T", 0.005),
                "stable_s": _optional_float(step, "stable_s", 0.0),
                "timeout_s": _optional_float(step, "timeout_s", 24 * 60 * 60),
            }
        if step_type == "ramp_to_zero":
            self._ensure_field_supported()
            rate_T_per_min = _required_float(step, "rate_T_per_min")
            self._validate_field(0.0, rate_T_per_min)
            return {
                "type": step_type,
                "rate_T_per_min": rate_T_per_min,
                "tolerance_T": _optional_float(step, "tolerance_T", 0.005),
                "stable_s": _optional_float(step, "stable_s", 0.0),
                "timeout_s": _optional_float(step, "timeout_s", 24 * 60 * 60),
            }
        if step_type == "wait":
            duration_s = _required_float(step, "duration_s")
            if duration_s <= 0 or duration_s > 30 * 24 * 60 * 60:
                raise ValueError("Wait duration must be between 0 and 2592000 seconds")
            return {"type": step_type, "duration_s": duration_s}
        if step_type in {"notice", "signal"}:
            signal = self._normalize_recipe_signal(step.get("signal") or "manual")
            message = str(step.get("message") or "Continue recipe")
            if len(message) > 300:
                raise ValueError("Notice message is too long")
            return {"type": "signal", "signal": signal, "message": message}
        if step_type == "external_measurement":
            mode = str(step.get("mode") or "").strip().lower()
            if mode not in {"point", "start", "stop"}:
                raise ValueError("external_measurement mode must be 'point', 'start', or 'stop'")
            message = str(step.get("message") or "Run external measurement")
            if len(message) > 300:
                raise ValueError("External measurement message is too long")
            timeout_s = _required_float(step, "timeout_s")
            if timeout_s <= 0 or timeout_s > 30 * 24 * 60 * 60:
                raise ValueError(
                    "External measurement timeout_s must be between 0 and 2592000 seconds"
                )
            return {
                "type": step_type,
                "mode": mode,
                "request_signal": self._normalize_recipe_signal(step.get("request_signal")),
                "completion_signal": self._normalize_recipe_signal(step.get("completion_signal")),
                "failure_signal": self._normalize_recipe_signal(step.get("failure_signal")),
                "timeout_s": timeout_s,
                "message": message,
            }
        raise ValueError(f"Unknown recipe step type: {step_type}")

    def _recipe_step_message(self, step: dict[str, Any]) -> str:
        step_type = step["type"]
        if step_type == "ramp_temperature":
            return f"Ramping {step['loop']} temperature to {step['target_K']} K"
        if step_type == "set_temperature_target":
            return f"Setting {step['loop']} temperature target to {step['target_K']} K"
        if step_type == "ramp_field":
            return f"Ramping field to {step['target_T']} T"
        if step_type == "ramp_to_zero":
            return "Ramping field to zero"
        if step_type == "wait":
            return f"Waiting {step['duration_s']} s"
        if step_type == "signal":
            return step.get("message") or f"Waiting for signal {step.get('signal')}"
        if step_type == "external_measurement":
            return (
                step.get("message")
                or f"Waiting for external measurement {step.get('request_signal')}"
            )
        return step_type

    async def _wait_for_temperature_step(self, step: dict[str, Any]) -> None:
        loop = step.get("loop", "both")
        target_K = step["target_K"]
        tolerance_K = step["tolerance_K"]
        targets = (
            {"sample": target_K, "vti": target_K * 0.9} if loop == "both" else {loop: target_K}
        )

        def condition() -> bool:
            for loop_name, loop_target_K in targets.items():
                loop_state = getattr(self._state.temperature, loop_name)
                temperature_K = loop_state.temperature_K
                if temperature_K is None:
                    return False
                if abs(temperature_K - loop_target_K) > tolerance_K:
                    return False
            return True

        await self._wait_for_recipe_condition(
            condition,
            step["timeout_s"],
            step["stable_s"],
            "temperature target",
        )

    async def _wait_for_field_step(
        self,
        target_T: float,
        step: dict[str, Any],
    ) -> None:
        tolerance_T = step["tolerance_T"]

        def condition() -> bool:
            field = self._state.field
            if field.B_T is None:
                return False
            if abs(field.B_T - target_T) > tolerance_T:
                return False
            if field.at_setpoint is False or field.ramping:
                return False
            return True

        await self._wait_for_recipe_condition(
            condition,
            step["timeout_s"],
            step["stable_s"],
            "field target",
        )

    async def _wait_for_recipe_condition(
        self,
        condition: Callable[[], bool],
        timeout_s: float,
        stable_s: float,
        label: str,
    ) -> None:
        started_at = monotonic()
        stable_since: float | None = None
        while True:
            await self.poll_once()
            now = monotonic()
            if condition():
                stable_since = stable_since or now
                if now - stable_since >= stable_s:
                    return
            else:
                stable_since = None
            if timeout_s > 0 and now - started_at > timeout_s:
                raise TimeoutError(f"Timed out waiting for {label}")
            await asyncio.sleep(max(0.25, self.config.poll_interval_s))

    def _normalize_recipe_signal(self, signal: Any) -> str:
        normalized = str(signal or "").strip()
        if not normalized:
            raise ValueError("Recipe signal cannot be empty")
        if len(normalized) > 80:
            raise ValueError("Recipe signal is too long")
        return normalized

    def _set_recipe_signal(self, signal: str) -> None:
        self._recipe_signal_events.setdefault(signal, asyncio.Event()).set()

    def _empty_recipe_status(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "name": None,
            "steps": [],
            "current_step_index": None,
            "current_step": None,
            "message": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "waiting_signal": None,
            "last_signal": None,
            "external_measurement": None,
        }

    def _recipe_dir(self) -> Path:
        return Path(self.config.recipe_dir)

    def _recipe_file_path(self, recipe_id: str) -> Path:
        normalized_id = _recipe_slug(recipe_id)
        path = self._safe_recipe_path(f"{normalized_id}.json")
        if not path.exists():
            raise ValueError(f"Unknown saved recipe: {recipe_id}")
        return path

    def _recipe_output_path(self, recipe_name: str) -> Path:
        return self._safe_recipe_path(f"{_recipe_slug(recipe_name)}.json")

    def _safe_recipe_path(self, filename: str) -> Path:
        recipe_dir = self._recipe_dir().resolve()
        path = (recipe_dir / filename).resolve()
        if path.parent != recipe_dir:
            raise ValueError(f"Recipe path escapes recipe_dir: {filename!r}")
        return path

    def _load_saved_recipe_file(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError as exc:
            raise ValueError(f"Unknown saved recipe: {path.stem}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Saved recipe {path.stem!r} is invalid")
        return self._normalized_recipe_definition(payload)

    def _normalized_recipe_definition(self, recipe: dict[str, Any]) -> dict[str, Any]:
        name = str(recipe.get("name") or "Recipe").strip() or "Recipe"
        steps = self._validate_recipe(recipe)
        return {
            "name": name,
            "steps": steps,
        }

    def _saved_recipe_summary(self, path: Path, recipe: dict[str, Any]) -> dict[str, Any]:
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        return {
            "id": path.stem,
            "name": recipe["name"],
            "step_count": len(recipe["steps"]),
            "updated_at": updated_at,
        }

    async def _publish_and_log(self, data: dict[str, Any]) -> None:
        await self._publish(data)
        now = monotonic()
        if now - self._last_log >= self.config.log_interval_s:
            try:
                await self._run_blocking(lambda: self._append_log(data))
            except OSError as exc:
                logger.warning("Failed to append cryostat environment log: %s", exc)
            else:
                self._last_log = now

    def _read_state_safely(self) -> CryostatState:
        try:
            return self.backend.read_state()
        except Exception as exc:
            logger.exception("Failed to read cryostat state from backend")
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
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Unexpected error in cryostat polling loop")
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
        row = _flatten_state(data)
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"cryostat_environment_{today}.csv"
        path = Path(self.config.log_dir) / filename
        path = _compatible_log_path(path, list(row.keys()))
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        with path.open("a", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(row.keys()))
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    def _validate_temperature(self, target_K: float, rate_K_per_min: float) -> None:
        self._validate_temperature_target(target_K)
        safety = self.config.safety
        if rate_K_per_min <= 0 or rate_K_per_min > safety.max_temperature_rate_K_per_min:
            raise ValueError(f"Temperature rate out of range: {rate_K_per_min} K/min")

    def _validate_temperature_target(self, target_K: float) -> None:
        safety = self.config.safety
        if not safety.min_temperature_K <= target_K <= safety.max_temperature_K:
            raise ValueError(f"Temperature target out of range: {target_K} K")

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

    def _validate_heater_percent(self, heater_percent: float) -> None:
        if not 0.0 <= heater_percent <= 100.0:
            raise ValueError("Heater output must be between 0 and 100 percent")

    def _validate_pid(self, p: float, i: float, d: float) -> None:
        if p < 0 or i < 0 or d < 0:
            raise ValueError("PID values must be non-negative")

    def _validate_pressure(self, pressure_mbar: float) -> None:
        if pressure_mbar < 0:
            raise ValueError("Pressure target must be non-negative")

    def _ensure_writable(self) -> None:
        if self.config.read_only:
            raise PermissionError("Cryostat service is running in read-only mode")

    def config_snapshot(self) -> dict[str, Any]:
        return self.config.to_dict()

    async def activate_insert_profile(self, profile_id: str) -> dict[str, Any]:
        self._ensure_writable()
        try:
            self.config.apply_insert_profile(profile_id)
        except KeyError as exc:
            raise ValueError(exc.args[0]) from exc
        async with self._hardware_lock:
            await self._run_blocking(self._replace_backend)
        await self._publish_and_log(self.state_snapshot())
        return self.config_snapshot()

    def _capabilities(self) -> InsertCapabilitiesConfig:
        return self.config.active_capabilities()

    def _ensure_capability(self, capability: str, message: str) -> None:
        if getattr(self._capabilities(), capability, True) is False:
            raise PermissionError(message)

    def _ensure_field_supported(self) -> None:
        self._ensure_capability(
            "field_control",
            "Field control is not supported for the active insert",
        )

    def _ensure_temperature_supported(self, loop: str) -> None:
        self._ensure_capability(
            "temperature_control",
            "Temperature control is not supported for the active insert",
        )
        if loop in {"sample", "both"}:
            self._ensure_capability(
                "sample_loop",
                "Sample temperature loop is not supported for the active insert",
            )
        if loop in {"vti", "both"}:
            self._ensure_capability(
                "vti_loop",
                "VTI temperature loop is not supported for the active insert",
            )

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

    async def visa_resources(self) -> dict[str, Any]:
        async with self._hardware_lock:
            return await self._run_blocking(list_visa_resources)

    async def catalog(self) -> dict[str, Any]:
        async with self._hardware_lock:
            return await self._run_blocking(self.backend.catalog)

    async def raw_readings(self) -> dict[str, Any]:
        async with self._hardware_lock:
            return await self._run_blocking(self.backend.raw_readings)

    async def diagnostic_query(self, target: str, command: str) -> dict[str, Any]:
        normalized = command.strip()
        if not normalized.startswith("READ:"):
            raise ValueError("Diagnostic query only allows READ commands")
        if target not in {"itc", "ips"}:
            raise ValueError("Diagnostic target must be 'itc' or 'ips'")
        async with self._hardware_lock:
            return await self._run_blocking(
                lambda: self.backend.diagnostic_query(target, normalized)
            )

    def _replace_backend(self) -> None:
        self.backend.close()
        self.backend = create_backend(self.config)
        self._state = self._read_state_safely()

    async def _run_blocking(self, operation: Callable[[], Any]) -> Any:
        if self.config.backend in {"mercury", "heliox"}:
            return await asyncio.to_thread(operation)
        return operation()


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
        "sample_heater_mode": sample["heater_mode"],
        "sample_loop_enabled": sample["loop_enabled"],
        "sample_ramp_enabled": sample["ramp_enabled"],
        "sample_target_reached": sample["target_reached"],
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
        "vti_heater_mode": vti["heater_mode"],
        "vti_loop_enabled": vti["loop_enabled"],
        "vti_ramp_enabled": vti["ramp_enabled"],
        "vti_target_reached": vti["target_reached"],
        "vti_mode": vti["mode"],
        "vti_stable": vti["stable"],
        "vti_ramping": vti["ramping"],
        "B_T": data["field"]["B_T"],
        "field_target_T": data["field"]["target_T"],
        "field_rate_T_per_min": data["field"]["rate_T_per_min"],
        "field_output_current_A": data["field"]["output_current_A"],
        "field_output_voltage_V": data["field"]["output_voltage_V"],
        "magnet_temperature_K": data["field"]["magnet_temperature_K"],
        "pt1_temperature_K": data["field"]["pt1_temperature_K"],
        "pt2_temperature_K": data["field"]["pt2_temperature_K"],
        "field_action": data["field"]["action"],
        "field_at_setpoint": data["field"]["at_setpoint"],
        "field_at_zero": data["field"]["at_zero"],
        "field_clamped": data["field"]["clamped"],
        "field_stable": data["field"]["stable"],
        "field_ramping": data["field"]["ramping"],
        "switch_heater_status": data["switch_heater"]["status"],
        "switch_heater_target_status": data["switch_heater"]["target_status"],
        "switch_heater_ready": data["switch_heater"]["ready"],
        "switch_heater_delay_s": data["switch_heater"]["delay_s"],
        "switch_heater_elapsed_s": data["switch_heater"]["elapsed_s"],
        "pressure_mbar": data["pressure"]["mbar"],
        "pressure_target_mbar": data["pressure"]["target_mbar"],
        "needle_valve_percent": data["pressure"]["needle_valve_percent"],
        "pressure_mode": data["pressure"]["mode"],
        "safety_level": data["safety"]["level"],
        "safety_message": data["safety"]["message"],
    }


def _required_float(data: dict[str, Any], key: str) -> float:
    try:
        return float(data[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Recipe step requires a numeric {key}") from exc


def _optional_float(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key)
    if value is None or value == "":
        return default
    return _required_float(data, key)


def _compatible_log_path(path: Path, fieldnames: list[str]) -> Path:
    if _csv_header_matches(path, fieldnames):
        return path
    return path.with_name(f"{path.stem}_v2{path.suffix}")


def _csv_header_matches(path: Path, fieldnames: list[str]) -> bool:
    if not path.exists():
        return True
    with path.open(newline="") as file:
        reader = csv.reader(file)
        try:
            header = next(reader)
        except StopIteration:
            return True
    return header == fieldnames


def _recipe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug[:80] or "recipe"
