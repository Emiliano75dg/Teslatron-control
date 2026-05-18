import asyncio
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from teslatron_services.cryostat.api import create_app
from teslatron_services.cryostat.config import (
    CryostatServiceConfig,
    InsertCapabilitiesConfig,
    InsertProfileConfig,
    MercurySensorSetupConfig,
    config_from_mapping,
)
from teslatron_services.cryostat.service import CryostatService
from teslatron_services.cryostat.state import (
    CryostatState,
    FieldState,
    TemperatureLoopState,
    TemperatureState,
)


class FakeBackend:
    def __init__(self):
        self.calls = []

    def close(self) -> None:
        return None

    def read_state(self) -> CryostatState:
        return CryostatState(
            temperature=TemperatureState(
                sample=TemperatureLoopState(temperature_K=295.0, target_K=295.0, stable=True),
                vti=TemperatureLoopState(temperature_K=265.5, target_K=265.5, stable=True),
            ),
            field=FieldState(B_T=0.0, target_T=0.0, at_setpoint=True, stable=True),
        )

    def ramp_temperature(self, target_K: float, rate_K_per_min: float, loop: str = "both") -> None:
        self.calls.append(("ramp_temperature", target_K, rate_K_per_min, loop))

    def set_temperature_target(self, target_K: float, loop: str = "both") -> None:
        self.calls.append(("set_temperature_target", target_K, loop))

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self.calls.append(("ramp_field", target_T, rate_T_per_min))

    def ramp_to_zero(self, rate_T_per_min: float) -> None:
        self.calls.append(("ramp_to_zero", rate_T_per_min))

    def clamp(self) -> None:
        self.calls.append(("clamp",))

    def hold(self) -> None:
        self.calls.append(("hold",))

    def abort(self) -> None:
        self.calls.append(("abort",))

    def set_vti_needle(self, needle_valve_percent: float) -> None:
        self.calls.append(("set_vti_needle", needle_valve_percent))

    def set_vti_pressure(self, pressure_mbar: float) -> None:
        self.calls.append(("set_vti_pressure", pressure_mbar))

    def set_temperature_fixed_heater(self, loop: str, heater_percent: float) -> None:
        self.calls.append(("set_temperature_fixed_heater", loop, heater_percent))

    def set_temperature_pid(
        self,
        loop: str,
        p: float,
        i: float,
        d: float,
        auto: bool = False,
    ) -> None:
        self.calls.append(("set_temperature_pid", loop, p, i, d, auto))

    def set_switch_heater(self, enabled: bool) -> None:
        self.calls.append(("set_switch_heater", enabled))

    def apply_sample_sensor(self, sensor: MercurySensorSetupConfig) -> None:
        self.calls.append(("apply_sample_sensor", sensor.calibration))

    def diagnostics(self) -> dict:
        return {}

    def catalog(self) -> dict:
        return {}

    def raw_readings(self) -> dict:
        return {}

    def diagnostic_query(self, target: str, command: str) -> dict:
        return {"target": target, "command": command}


class SlowBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.active_operations = 0
        self.max_active_operations = 0

    def ramp_field(self, target_T: float, rate_T_per_min: float) -> None:
        self.active_operations += 1
        self.max_active_operations = max(self.max_active_operations, self.active_operations)
        try:
            time.sleep(0.02)
            super().ramp_field(target_T, rate_T_per_min)
        finally:
            self.active_operations -= 1


class CountingBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.read_state_calls = 0

    def read_state(self) -> CryostatState:
        self.read_state_calls += 1
        return super().read_state()


class MissingValueBackend(FakeBackend):
    def read_state(self) -> CryostatState:
        return CryostatState(
            temperature=TemperatureState(
                sample=TemperatureLoopState(temperature_K=None, target_K=None, stable=False),
                vti=TemperatureLoopState(temperature_K=None, target_K=None, stable=False),
            ),
            field=FieldState(B_T=None, target_T=None, at_setpoint=None, stable=False),
        )


class CryostatServiceCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def make_service(
        self, *, capabilities: InsertCapabilitiesConfig | None = None
    ) -> CryostatService:
        config = CryostatServiceConfig(
            insert_profiles={
                "limited": InsertProfileConfig(
                    name="Limited",
                    capabilities=capabilities or InsertCapabilitiesConfig(),
                    sample_sensor_options=["cernox_a"],
                    default_sample_sensor="cernox_a",
                )
            },
            sample_sensor_presets={
                "cernox_a": MercurySensorSetupConfig(
                    sensor_type="CERNOX",
                    excitation_type="CURR",
                    excitation_magnitude="10uA",
                    calibration="X205007",
                )
            },
            active_insert="limited",
        )
        config.apply_insert_profile("limited")
        return CryostatService(config, backend=FakeBackend())

    async def test_field_command_blocked_by_insert_capability(self) -> None:
        service = self.make_service(capabilities=InsertCapabilitiesConfig(field_control=False))

        with self.assertRaises(PermissionError):
            await service.ramp_field(1.0, 0.1)

        self.assertEqual(service.backend.calls, [])

    async def test_field_command_ignores_unrelated_temperature_capability(self) -> None:
        service = self.make_service(
            capabilities=InsertCapabilitiesConfig(temperature_control=False)
        )

        await service.ramp_field(1.0, 0.1)

        self.assertEqual(service.backend.calls, [("ramp_field", 1.0, 0.1)])

    async def test_hardware_commands_are_serialized(self) -> None:
        backend = SlowBackend()
        service = self.make_service()
        service.backend = backend

        await asyncio.gather(
            service.ramp_field(1.0, 0.1),
            service.ramp_field(2.0, 0.1),
        )

        self.assertEqual(backend.max_active_operations, 1)
        self.assertEqual(
            backend.calls,
            [
                ("ramp_field", 1.0, 0.1),
                ("ramp_field", 2.0, 0.1),
            ],
        )

    async def test_vti_loop_blocked_for_both_temperature_command(self) -> None:
        service = self.make_service(capabilities=InsertCapabilitiesConfig(vti_loop=False))

        with self.assertRaises(PermissionError):
            await service.ramp_temperature(4.2, 0.5, loop="both")

        self.assertEqual(service.backend.calls, [])

    async def test_apply_sample_sensor_uses_active_insert_preset_list(self) -> None:
        service = self.make_service()

        snapshot = await service.apply_sample_sensor("cernox_a")

        self.assertEqual(service.backend.calls, [("apply_sample_sensor", "X205007")])
        self.assertEqual(snapshot["active_sample_sensor"], "cernox_a")

    async def test_apply_sample_sensor_rejects_preset_not_allowed_for_insert(self) -> None:
        service = self.make_service()
        service.config.sample_sensor_presets["other"] = MercurySensorSetupConfig(
            sensor_type="CERNOX",
            excitation_type="CURR",
            excitation_magnitude="3uA",
            calibration="X999999",
        )

        with self.assertRaises(ValueError):
            await service.apply_sample_sensor("other")

    async def test_recipe_runs_steps_in_order(self) -> None:
        service = self.make_service()

        status = await service.start_recipe(
            {
                "name": "test",
                "steps": [
                    {
                        "type": "ramp_temperature",
                        "loop": "sample",
                        "target_K": 295.0,
                        "rate_K_per_min": 0.5,
                    },
                    {"type": "wait", "duration_s": 0.01},
                    {
                        "type": "ramp_field",
                        "target_T": 0.0,
                        "rate_T_per_min": 0.1,
                    },
                ],
            }
        )

        self.assertEqual(status["status"], "running")
        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "completed")
        self.assertEqual(
            service.backend.calls,
            [
                ("ramp_temperature", 295.0, 0.5, "sample"),
                ("ramp_field", 0.0, 0.1),
            ],
        )

    async def test_recipe_signal_waits_for_external_completion(self) -> None:
        service = self.make_service()

        await service.start_recipe(
            {
                "name": "signal",
                "steps": [
                    {
                        "type": "signal",
                        "signal": "measurement_done",
                        "message": "Check contacts",
                    },
                    {
                        "type": "ramp_field",
                        "target_T": 0.0,
                        "rate_T_per_min": 0.1,
                    },
                ],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_signal":
                break
            await asyncio.sleep(0.01)

        self.assertEqual(service.recipe_status()["status"], "waiting_signal")
        self.assertEqual(service.backend.calls, [])

        await service.signal_recipe("measurement_done", "Keithley sweep complete")
        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "completed")
        self.assertEqual(service.backend.calls, [("ramp_field", 0.0, 0.1)])

    async def test_external_measurement_point_waits_for_completion_signal(self) -> None:
        service = self.make_service()

        await service.start_recipe(
            {
                "name": "point measurement",
                "steps": [
                    {
                        "type": "external_measurement",
                        "mode": "point",
                        "request_signal": "measure_iv",
                        "completion_signal": "measure_iv.completed",
                        "failure_signal": "measure_iv.failed",
                        "timeout_s": 5,
                        "message": "Run IV measurement in LabVIEW",
                    },
                    {
                        "type": "ramp_field",
                        "target_T": 0.0,
                        "rate_T_per_min": 0.1,
                    },
                ],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_external_measurement":
                break
            await asyncio.sleep(0.01)

        status = service.recipe_status()
        self.assertEqual(status["status"], "waiting_external_measurement")
        self.assertEqual(status["external_measurement"]["request_signal"], "measure_iv")
        self.assertEqual(service.backend.calls, [])

        await service.signal_recipe("measure_iv.completed", "IV finished")
        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "completed")
        self.assertEqual(service.backend.calls, [("ramp_field", 0.0, 0.1)])

    async def test_external_measurement_start_waits_for_started_then_advances_to_ramp(self) -> None:
        service = self.make_service()
        ramp_wait = asyncio.Event()
        release_ramp = asyncio.Event()
        original_wait_for_temperature_step = service._wait_for_temperature_step

        async def blocking_wait_for_temperature_step(step: dict) -> None:
            ramp_wait.set()
            await release_ramp.wait()
            await original_wait_for_temperature_step(step)

        service._wait_for_temperature_step = blocking_wait_for_temperature_step

        await service.start_recipe(
            {
                "name": "continuous measurement start",
                "steps": [
                    {
                        "type": "external_measurement",
                        "mode": "start",
                        "request_signal": "R_vs_T.start",
                        "completion_signal": "R_vs_T.started",
                        "failure_signal": "R_vs_T.failed",
                        "timeout_s": 5,
                        "message": "Start LabVIEW continuous acquisition",
                    },
                    {
                        "type": "ramp_temperature",
                        "loop": "sample",
                        "target_K": 295.0,
                        "rate_K_per_min": 0.5,
                        "stable_s": 0,
                    },
                ],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_external_measurement":
                break
            await asyncio.sleep(0.01)

        self.assertEqual(service.backend.calls, [])

        await service.signal_recipe("R_vs_T.started", "Continuous acquisition started")
        await asyncio.wait_for(ramp_wait.wait(), timeout=1)

        self.assertEqual(service.backend.calls, [("ramp_temperature", 295.0, 0.5, "sample")])
        self.assertEqual(service.recipe_status()["current_step"]["type"], "ramp_temperature")

        release_ramp.set()
        await service._recipe_task
        self.assertEqual(service.recipe_status()["status"], "completed")

    async def test_external_measurement_stop_waits_for_stopped_signal(self) -> None:
        service = self.make_service()

        await service.start_recipe(
            {
                "name": "continuous measurement stop",
                "steps": [
                    {
                        "type": "external_measurement",
                        "mode": "stop",
                        "request_signal": "R_vs_T.stop",
                        "completion_signal": "R_vs_T.stopped",
                        "failure_signal": "R_vs_T.failed",
                        "timeout_s": 5,
                        "message": "Stop LabVIEW continuous acquisition",
                    },
                    {"type": "wait", "duration_s": 0.01},
                ],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_external_measurement":
                break
            await asyncio.sleep(0.01)

        self.assertEqual(service.recipe_status()["external_measurement"]["mode"], "stop")

        await service.signal_recipe("R_vs_T.stopped", "Continuous acquisition stopped")
        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "completed")

    async def test_external_measurement_failure_signal_moves_recipe_to_error(self) -> None:
        service = self.make_service()

        await service.start_recipe(
            {
                "name": "external failure",
                "steps": [
                    {
                        "type": "external_measurement",
                        "mode": "point",
                        "request_signal": "measure_iv",
                        "completion_signal": "measure_iv.completed",
                        "failure_signal": "measure_iv.failed",
                        "timeout_s": 5,
                        "message": "Run IV measurement in LabVIEW",
                    }
                ],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_external_measurement":
                break
            await asyncio.sleep(0.01)

        await service.signal_recipe("measure_iv.failed", "Keithley compliance tripped")
        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "error")
        self.assertIn("Keithley compliance tripped", service.recipe_status()["message"])

    async def test_external_measurement_timeout_moves_recipe_to_error(self) -> None:
        service = self.make_service()

        await service.start_recipe(
            {
                "name": "external timeout",
                "steps": [
                    {
                        "type": "external_measurement",
                        "mode": "point",
                        "request_signal": "measure_iv",
                        "completion_signal": "measure_iv.completed",
                        "failure_signal": "measure_iv.failed",
                        "timeout_s": 0.05,
                        "message": "Run IV measurement in LabVIEW",
                    }
                ],
            }
        )

        await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "error")
        self.assertIn(
            "Timed out waiting for external measurement measure_iv",
            service.recipe_status()["message"],
        )

    async def test_recipe_rejects_second_active_recipe(self) -> None:
        service = self.make_service()
        await service.start_recipe(
            {
                "name": "waiting",
                "steps": [{"type": "notice", "message": "Pause"}],
            }
        )

        for _ in range(20):
            if service.recipe_status()["status"] == "waiting_signal":
                break
            await asyncio.sleep(0.01)

        with self.assertRaises(ValueError):
            await service.start_recipe(
                {
                    "name": "second",
                    "steps": [{"type": "wait", "duration_s": 1}],
                }
            )

        await service.abort_recipe()
        self.assertEqual(service.recipe_status()["status"], "aborted")


class CryostatRecipeStorageTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, recipe_dir: str, *, read_only: bool = False) -> CryostatService:
        config = CryostatServiceConfig(
            backend="mock",
            read_only=read_only,
            recipe_dir=recipe_dir,
        )
        return CryostatService(config, backend=FakeBackend())

    async def test_recipe_paths_stay_within_recipe_dir_for_malicious_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(tmpdir)
            recipe_dir = Path(tmpdir).resolve()

            for value in ["../../x", "..\\x", "", "a" * 300, "Misura \u03bc", "folder/name"]:
                output_path = service._recipe_output_path(value)
                self.assertEqual(output_path.parent, recipe_dir)
                self.assertTrue(output_path.name.endswith(".json"))

            with self.assertRaisesRegex(ValueError, "Unknown saved recipe"):
                service._recipe_file_path("../../x")

    async def test_save_recipe_does_not_overwrite_without_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(tmpdir)
            recipe = {"name": "IV Sweep", "steps": [{"type": "wait", "duration_s": 0.01}]}

            saved = await service.save_recipe(recipe)
            self.assertEqual(saved["name"], "IV Sweep")

            with self.assertRaisesRegex(ValueError, "already exists"):
                await service.save_recipe(recipe)

            overwritten = await service.save_recipe(
                {"name": "IV Sweep", "steps": [{"type": "wait", "duration_s": 0.02}]},
                overwrite=True,
            )
            self.assertEqual(overwritten["name"], "IV Sweep")

            payload = service.load_saved_recipe("iv_sweep")
            self.assertEqual(payload["steps"][0]["duration_s"], 0.02)

    async def test_load_saved_recipe_handles_unicode_and_slashes_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(tmpdir)
            summary = await service.save_recipe(
                {"name": "Prova/\u03bc sweep", "steps": [{"type": "wait", "duration_s": 0.01}]}
            )

            payload = service.load_saved_recipe(summary["id"])

            self.assertEqual(payload["name"], "Prova/\u03bc sweep")
            self.assertEqual(payload["steps"][0]["type"], "wait")


class CryostatApiTests(unittest.IsolatedAsyncioTestCase):
    def _app_for_service(self, service: CryostatService):
        app = create_app(config=service.config)
        app.state.cryostat = service
        return app

    def test_create_app_loads_repo_config_by_default(self) -> None:
        app = create_app()
        endpoint = next(
            route.endpoint for route in app.routes if getattr(route, "path", None) == "/config"
        )
        payload = asyncio.run(endpoint())
        self.assertEqual(payload["backend"], "standard")
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["active_insert"], "fisher_probe")

    def test_config_from_mapping_accepts_legacy_mercury_backend_alias(self) -> None:
        config = config_from_mapping({"cryostat": {"backend": "mercury"}})
        self.assertEqual(config.backend, "standard")

    async def test_health_state_and_recipes_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = CryostatService(
                CryostatServiceConfig(backend="mock", recipe_dir=tmpdir),
                backend=FakeBackend(),
            )
            app = self._app_for_service(service)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                save_response = await client.post(
                    "/recipes/save",
                    json={"name": "api recipe", "steps": [{"type": "wait", "duration_s": 0.01}]},
                )
                self.assertEqual(save_response.status_code, 200)

                health = await client.get("/health")
                state = await client.get("/state")
                recipes = await client.get("/recipes")

                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})
                self.assertEqual(state.status_code, 200)
                self.assertEqual(recipes.status_code, 200)
                self.assertEqual(recipes.json()["recipes"][0]["name"], "api recipe")

    async def test_measurement_context_returns_cached_values_without_hardware_poll(self) -> None:
        backend = CountingBackend()
        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=backend)
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/measurement-context")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["sample_temperature_K"],
            295.0,
        )
        self.assertEqual(response.json()["field_T"], 0.0)
        self.assertTrue(response.json()["safe_to_measure"])
        self.assertEqual(backend.read_state_calls, 1)

    async def test_measurement_context_returns_null_for_missing_values(self) -> None:
        service = CryostatService(
            CryostatServiceConfig(backend="mock"), backend=MissingValueBackend()
        )
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/measurement-context")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["sample_temperature_K"])
        self.assertIsNone(response.json()["field_T"])
        self.assertTrue(response.json()["safe_to_measure"])

    async def test_external_measurement_pending_and_complete_endpoints(self) -> None:
        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=FakeBackend())
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post(
                "/recipes/start",
                json={
                    "name": "external api",
                    "steps": [
                        {
                            "type": "external_measurement",
                            "mode": "point",
                            "request_signal": "measure_iv",
                            "completion_signal": "measure_iv.completed",
                            "failure_signal": "measure_iv.failed",
                            "timeout_s": 5,
                            "message": "Run IV measurement in LabVIEW",
                        },
                        {"type": "wait", "duration_s": 0.01},
                    ],
                },
            )

            for _ in range(20):
                pending = await client.get("/external-measurements/pending")
                if pending.json()["pending"]:
                    break
                await asyncio.sleep(0.01)

            pending_payload = pending.json()
            self.assertTrue(pending_payload["pending"])
            self.assertEqual(pending_payload["mode"], "point")
            self.assertEqual(pending_payload["request_signal"], "measure_iv")
            self.assertEqual(pending_payload["sample_temperature_K"], 295.0)
            self.assertEqual(pending_payload["field_T"], 0.0)
            self.assertTrue(pending_payload["safe_to_measure"])

            complete = await client.post(
                "/external-measurements/complete",
                json={
                    "request_signal": "measure_iv",
                    "status": "completed",
                    "message": "Saved data",
                    "metadata": {
                        "file_path": "/tmp/iv.csv",
                        "points": 123,
                        "instrument": "LabVIEW",
                    },
                },
            )

            self.assertEqual(complete.status_code, 200)
            await service._recipe_task

        self.assertEqual(service.recipe_status()["status"], "completed")
        self.assertEqual(service.recipe_status()["last_signal"]["signal"], "measure_iv.completed")
        self.assertEqual(service.recipe_status()["last_signal"]["metadata"]["points"], 123)

    async def test_measurement_context_polling_during_ramp_does_not_change_recipe_state(
        self,
    ) -> None:
        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=FakeBackend())
        ramp_wait = asyncio.Event()
        release_ramp = asyncio.Event()
        original_wait_for_temperature_step = service._wait_for_temperature_step

        async def blocking_wait_for_temperature_step(step: dict) -> None:
            ramp_wait.set()
            await release_ramp.wait()
            await original_wait_for_temperature_step(step)

        service._wait_for_temperature_step = blocking_wait_for_temperature_step
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post(
                "/recipes/start",
                json={
                    "name": "ramp polling",
                    "steps": [
                        {
                            "type": "external_measurement",
                            "mode": "start",
                            "request_signal": "R_vs_T.start",
                            "completion_signal": "R_vs_T.started",
                            "failure_signal": "R_vs_T.failed",
                            "timeout_s": 5,
                            "message": "Start LabVIEW continuous acquisition",
                        },
                        {
                            "type": "ramp_temperature",
                            "loop": "sample",
                            "target_K": 295.0,
                            "rate_K_per_min": 0.5,
                            "stable_s": 0,
                        },
                    ],
                },
            )
            await client.post(
                "/recipes/signal",
                json={"signal": "R_vs_T.started", "message": "Started"},
            )
            await asyncio.wait_for(ramp_wait.wait(), timeout=1)

            before = service.recipe_status()["current_step_index"]
            for _ in range(3):
                response = await client.get("/measurement-context")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(service.recipe_status()["current_step_index"], before)
                self.assertEqual(
                    service.recipe_status()["current_step"]["type"], "ramp_temperature"
                )

        release_ramp.set()
        await service._recipe_task

    async def test_recipe_signal_endpoint_persists_metadata_in_last_signal(self) -> None:
        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=FakeBackend())
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/recipes/signal",
                json={
                    "signal": "measure_iv.completed",
                    "message": "Completed",
                    "metadata": {"file_path": "/tmp/iv.csv", "instrument": "LabVIEW"},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            service.recipe_status()["last_signal"]["metadata"]["instrument"], "LabVIEW"
        )

    async def test_recipes_save_returns_403_in_read_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = CryostatService(
                CryostatServiceConfig(backend="mock", recipe_dir=tmpdir, read_only=True),
                backend=FakeBackend(),
            )
            app = self._app_for_service(service)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/recipes/save",
                    json={"name": "blocked", "steps": [{"type": "wait", "duration_s": 0.01}]},
                )

            self.assertEqual(response.status_code, 403)
            self.assertIn("read-only mode", response.json()["detail"])

    async def test_recipes_save_returns_400_for_duplicate_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = CryostatService(
                CryostatServiceConfig(backend="mock", recipe_dir=tmpdir),
                backend=FakeBackend(),
            )
            app = self._app_for_service(service)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                payload = {"name": "duplicate", "steps": [{"type": "wait", "duration_s": 0.01}]}
                self.assertEqual(
                    (await client.post("/recipes/save", json=payload)).status_code, 200
                )
                response = await client.post("/recipes/save", json=payload)

            self.assertEqual(response.status_code, 400)
            self.assertIn("already exists", response.json()["detail"])

    async def test_diagnostics_query_returns_400_for_invalid_command(self) -> None:
        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=FakeBackend())
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/diagnostics/query",
                json={"target": "itc", "command": "SET:DEV:BAD"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("only allows READ", response.json()["detail"])

    async def test_diagnostics_catalog_returns_502_for_backend_failure(self) -> None:
        async def fail_catalog() -> dict:
            raise RuntimeError("catalog unavailable")

        service = CryostatService(CryostatServiceConfig(backend="mock"), backend=FakeBackend())
        service.catalog = fail_catalog
        app = self._app_for_service(service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/diagnostics/catalog")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "catalog unavailable")

    def test_shutdown_route_is_disabled_by_default(self) -> None:
        app = create_app(config=CryostatServiceConfig())

        shutdown_routes = [
            route for route in app.routes if getattr(route, "path", None) == "/shutdown"
        ]

        self.assertEqual(shutdown_routes, [])

    def test_shutdown_route_calls_callback_when_enabled(self) -> None:
        called = {"value": 0}

        def shutdown_callback() -> None:
            called["value"] += 1

        app = create_app(
            config=CryostatServiceConfig(enable_shutdown=True),
            shutdown_callback=shutdown_callback,
        )

        endpoint = next(
            route.endpoint for route in app.routes if getattr(route, "path", None) == "/shutdown"
        )
        payload = asyncio.run(endpoint())

        self.assertEqual(payload, {"status": "shutting_down"})
        self.assertEqual(called["value"], 1)


class CryostatConfigTests(unittest.TestCase):
    def test_default_config_builds_fallback_insert_profile(self) -> None:
        config = config_from_mapping({})

        self.assertEqual(config.active_insert, "fisher_probe")
        self.assertIn("fisher_probe", config.insert_profiles)
        self.assertEqual(config.available_sample_sensor_presets(), {})

    def test_insert_switch_changes_sample_thermometer_but_not_ips(self) -> None:
        config = CryostatServiceConfig(
            sample_thermometer="initial",
            ips=config_from_mapping({}).ips,
            insert_profiles={
                "a": InsertProfileConfig(
                    sample_thermometer="thermo A",
                    itc=config_from_mapping({}).itc,
                ),
                "b": InsertProfileConfig(
                    sample_thermometer="thermo B",
                    itc=config_from_mapping({"cryostat": {"itc": {"probe_signal": "DB7.T1"}}}).itc,
                ),
            },
        )
        original_ips = config.ips

        config.apply_insert_profile("a")
        self.assertEqual(config.sample_thermometer, "thermo A")
        self.assertIs(config.ips, original_ips)

        config.apply_insert_profile("b")
        self.assertEqual(config.sample_thermometer, "thermo B")
        self.assertEqual(config.itc.probe_signal, "DB7.T1")
        self.assertIs(config.ips, original_ips)
        self.assertEqual(config.ips.magnet_group, "GRPZ")

    def test_field_control_capability_can_be_disabled_from_config(self) -> None:
        config = config_from_mapping(
            {
                "cryostat": {
                    "active_insert": "no_field",
                    "insert_profiles": {"no_field": {"capabilities": {"field_control": False}}},
                }
            }
        )

        self.assertFalse(config.active_capabilities().field_control)

    def test_insert_profile_with_ips_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot override IPS settings"):
            config_from_mapping(
                {"cryostat": {"insert_profiles": {"bad": {"ips": {"magnet_group": "GRPX"}}}}}
            )

    def test_profile_without_sensor_whitelist_has_no_available_presets(self) -> None:
        config = config_from_mapping(
            {
                "cryostat": {
                    "active_insert": "probe_a",
                    "sample_sensor_presets": {
                        "sensor_a": {
                            "sensor_type": "CERNOX",
                            "excitation_type": "CURR",
                            "excitation_magnitude": "10uA",
                            "calibration": "X205007",
                        }
                    },
                    "insert_profiles": {
                        "probe_a": {
                            "sample_thermometer": "Probe A",
                        }
                    },
                }
            }
        )

        self.assertEqual(config.available_sample_sensor_presets(), {})
        self.assertIsNone(config.active_sample_sensor)

    def test_default_sample_sensor_must_be_in_whitelist(self) -> None:
        with self.assertRaisesRegex(ValueError, "not listed in sample_sensor_options"):
            config_from_mapping(
                {
                    "cryostat": {
                        "sample_sensor_presets": {
                            "sensor_a": {
                                "sensor_type": "CERNOX",
                                "excitation_type": "CURR",
                                "excitation_magnitude": "10uA",
                                "calibration": "X205007",
                            }
                        },
                        "insert_profiles": {
                            "probe_a": {
                                "sample_sensor_options": [],
                                "default_sample_sensor": "sensor_a",
                            }
                        },
                    }
                }
            )

    def test_invalid_poll_interval_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "poll_interval_s must be > 0"):
            CryostatServiceConfig(poll_interval_s=0)

    def test_invalid_log_interval_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "log_interval_s must be >= 0"):
            CryostatServiceConfig(log_interval_s=-1)

    def test_active_insert_must_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown active_insert"):
            CryostatServiceConfig(
                active_insert="missing",
                insert_profiles={"known": InsertProfileConfig()},
            )

    def test_active_sample_sensor_must_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown active_sample_sensor"):
            CryostatServiceConfig(
                active_insert="probe_a",
                insert_profiles={"probe_a": InsertProfileConfig()},
                active_sample_sensor="missing",
            )

    def test_safety_temperature_range_must_be_coherent(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "min_temperature_K cannot exceed max_temperature_K"
        ):
            config_from_mapping(
                {
                    "cryostat": {
                        "safety": {
                            "min_temperature_K": 10.0,
                            "max_temperature_K": 5.0,
                        }
                    }
                }
            )

    def test_itc_timeout_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "ITC timeout_ms must be > 0"):
            config_from_mapping(
                {
                    "cryostat": {
                        "itc": {"timeout_ms": 0},
                    }
                }
            )
