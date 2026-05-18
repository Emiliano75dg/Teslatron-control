import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from teslatron_services.electrical.api import create_app
from teslatron_services.electrical.config import (
    CryostatEndpointConfig,
    ElectricalServiceConfig,
    InstrumentConfig,
    MeasurementPlanConfig,
    MeasurementSessionConfig,
    MeasurementStepConfig,
    PlanCompletionConfig,
    PlanTriggerConfig,
    config_from_mapping,
)
from teslatron_services.electrical.orchestrator import ElectricalMeasurementService
from teslatron_services.electrical.persistence import (
    ElectricalCsvMeasurementWriter,
    flatten_measurement,
)


class FakeDriver:
    def __init__(self):
        self.connected = False
        self.measurements = 0

    def connect(self) -> None:
        self.connected = True

    def shutdown(self) -> None:
        self.connected = False

    def measure(self) -> dict:
        self.measurements += 1
        return {"value": float(self.measurements), "unit": "A"}


class ElectricalServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_service(
        self,
        *,
        safe_to_measure: bool = True,
        save_dir: str | None = None,
        plans: dict[str, MeasurementPlanConfig] | None = None,
        recipe_notifier=None,
    ) -> ElectricalMeasurementService:
        config = ElectricalServiceConfig(
            cryostat=CryostatEndpointConfig(),
            measurement_session=MeasurementSessionConfig(
                save_dir=save_dir or "data/test-electrical"
            ),
            instruments={"mock_meter": InstrumentConfig()},
            plans=plans or {},
        )

        async def fetch_cryostat() -> dict:
            return {
                "timestamp": 123.0,
                "temperature": {
                    "sample": {"temperature_K": 4.2},
                    "vti": {"temperature_K": 4.3},
                },
                "field": {"B_T": 1.5},
                "pressure": {"mbar": 1e-5},
                "safety": {"safe_to_measure": safe_to_measure},
            }

        return ElectricalMeasurementService(
            config,
            cryostat_fetcher=fetch_cryostat,
            recipe_notifier=recipe_notifier,
            instruments={"mock_meter": FakeDriver()},
        )

    async def test_periodic_run_saves_measurements_with_cryostat_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(save_dir=tmpdir)
            await service.start()
            try:
                status = await service.start_periodic_run(
                    run_id="test_run",
                    instrument="mock_meter",
                    interval_s=0.01,
                    max_points=1,
                )
                self.assertEqual(status["status"], "running")
                await asyncio.wait_for(service._run_task, timeout=5)
                snapshot = service.state_snapshot()
                self.assertEqual(snapshot["run"]["status"], "completed")
                self.assertEqual(snapshot["run"]["points_acquired"], 1)
                path = Path(snapshot["run"]["output_path"])
                self.assertTrue(path.exists())
                lines = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(len(lines), 1)
                self.assertEqual(lines[0]["cryostat"]["sample_temperature_K"], 4.2)
                self.assertTrue(lines[0]["cryostat"]["safe_to_measure"])
                csv_path = Path(snapshot["run"]["electrical_csv_path"])
                self.assertTrue(csv_path.exists())
                rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["sample_temperature_K"], "4.2")
                self.assertEqual(rows[0]["field_T"], "1.5")
                self.assertEqual(rows[0]["value"], "1.0")
                self.assertEqual(snapshot["run"]["output_paths"]["jsonl"], str(path))
                self.assertEqual(snapshot["run"]["output_paths"]["electrical_csv"], str(csv_path))
                metadata_path = Path(snapshot["run"]["metadata_path"])
                self.assertTrue(metadata_path.exists())
                self.assertEqual(snapshot["run"]["output_paths"]["metadata"], str(metadata_path))
                self.assertEqual(path.parent, csv_path.parent)
                self.assertEqual(path.parent, metadata_path.parent)
                self.assertTrue(Path(snapshot["run"]["output_paths"]["config_snapshot"]).exists())
                self.assertTrue(Path(snapshot["run"]["output_paths"]["cryostat_snapshot_start"]).exists())
                self.assertTrue(Path(snapshot["run"]["output_paths"]["cryostat_snapshot_end"]).exists())
            finally:
                await asyncio.wait_for(service.stop(), timeout=5)

    async def test_run_start_creates_metadata_and_initial_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(save_dir=tmpdir, safe_to_measure=False)
            await service.start()
            try:
                status = await service.start_periodic_run(
                    run_id="metadata_run",
                    instrument="mock_meter",
                    interval_s=0.5,
                    max_points=1,
                )
                metadata_path = Path(status["metadata_path"])
                start_snapshot_path = Path(status["output_paths"]["cryostat_snapshot_start"])
                config_snapshot_path = Path(status["output_paths"]["config_snapshot"])

                self.assertTrue(metadata_path.exists())
                self.assertTrue(start_snapshot_path.exists())
                self.assertTrue(config_snapshot_path.exists())

                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(metadata["run_id"], "metadata_run")
                self.assertEqual(metadata["status"], "running")
                self.assertEqual(metadata["points_acquired"], 0)
                self.assertEqual(metadata["initial_cryostat_snapshot"]["field"]["B_T"], 1.5)
            finally:
                await asyncio.wait_for(service.stop(), timeout=5)

    async def test_run_waits_until_safe_to_measure(self) -> None:
        state = {"safe": False}
        config = ElectricalServiceConfig(
            measurement_session=MeasurementSessionConfig(save_dir="data/test-electrical"),
            instruments={"mock_meter": InstrumentConfig()},
        )

        async def fetch_cryostat() -> dict:
            return {
                "timestamp": 123.0,
                "temperature": {"sample": {"temperature_K": 4.2}, "vti": {"temperature_K": 4.3}},
                "field": {"B_T": 1.5},
                "pressure": {"mbar": 1e-5},
                "safety": {"safe_to_measure": state["safe"]},
            }

        driver = FakeDriver()
        service = ElectricalMeasurementService(
            config,
            cryostat_fetcher=fetch_cryostat,
            instruments={"mock_meter": driver},
        )
        await service.start()
        try:
            await service.start_periodic_run(
                run_id="blocked_run",
                instrument="mock_meter",
                interval_s=0.01,
                max_points=1,
            )
            await asyncio.sleep(0.05)
            self.assertEqual(driver.measurements, 0)
            state["safe"] = True
            await asyncio.wait_for(service._run_task, timeout=5)
            self.assertEqual(driver.measurements, 1)
        finally:
            await asyncio.wait_for(service.stop(), timeout=5)

    async def test_recipe_signal_runs_plan_and_notifies_cryostat_on_completion(self) -> None:
        notifications = []

        async def notify(signal: str, message: str | None) -> dict:
            notifications.append({"signal": signal, "message": message})
            return {"ok": True}

        plan = MeasurementPlanConfig(
            id="iv_mock",
            trigger=PlanTriggerConfig(type="recipe_signal", signal="measure_iv"),
            steps=[MeasurementStepConfig(instrument="mock_meter", action="measure")],
            completion=PlanCompletionConfig(
                notify_recipe=True,
                success_signal="measure_iv.completed",
                failure_signal="measure_iv.failed",
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(
                save_dir=tmpdir,
                plans={"iv_mock": plan},
                recipe_notifier=notify,
            )
            await service.start()
            try:
                status = await service.trigger_recipe_signal("measure_iv", "Start IV")
                self.assertEqual(status["status"], "running")
                await asyncio.wait_for(service._run_task, timeout=5)
                snapshot = service.state_snapshot()
                self.assertEqual(snapshot["run"]["status"], "completed")
                self.assertEqual(snapshot["run"]["plan_id"], "iv_mock")
                self.assertEqual(snapshot["run"]["trigger_signal"], "measure_iv")
                self.assertEqual(len(notifications), 1)
                self.assertEqual(notifications[0]["signal"], "measure_iv.completed")
            finally:
                await asyncio.wait_for(service.stop(), timeout=5)

    async def test_recipe_signal_requires_matching_plan(self) -> None:
        service = self.make_service()
        await service.start()
        try:
            with self.assertRaises(ValueError):
                await service.trigger_recipe_signal("measure_iv")
        finally:
            await asyncio.wait_for(service.stop(), timeout=5)

    async def test_acquire_measurement_tracks_relative_time_and_writes_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(save_dir=tmpdir)
            await service.start()
            try:
                with mock.patch(
                    "teslatron_services.electrical.orchestrator.monotonic",
                    side_effect=[10.0, 10.532],
                ):
                    await service.start_periodic_run(
                        run_id="timed_run",
                        instrument="mock_meter",
                        interval_s=60,
                        max_points=1,
                    )
                    await asyncio.wait_for(service._run_task, timeout=5)
                snapshot = service.state_snapshot()
                jsonl_path = Path(snapshot["run"]["output_path"])
                csv_path = Path(snapshot["run"]["electrical_csv_path"])
                self.assertTrue(jsonl_path.exists())
                self.assertTrue(csv_path.exists())
                event = snapshot["run"]["last_event"]
                self.assertEqual(event["jsonl_path"], str(jsonl_path))
                self.assertEqual(event["electrical_csv_path"], str(csv_path))
                rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["run_id"], "timed_run")
                self.assertEqual(rows[0]["instrument"], "mock_meter")
                self.assertEqual(rows[0]["sample_temperature_K"], "4.2")
                self.assertEqual(rows[0]["field_T"], "1.5")
                self.assertAlmostEqual(float(rows[0]["time_relative_s"]), 0.532, places=6)
            finally:
                await asyncio.wait_for(service.stop(), timeout=5)

    async def test_stop_run_writes_final_cryostat_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self.make_service(save_dir=tmpdir, safe_to_measure=False)
            await service.start()
            try:
                await service.start_periodic_run(
                    run_id="stop_run",
                    instrument="mock_meter",
                    interval_s=1.0,
                    max_points=5,
                )
                status = await service.stop_run()

                self.assertEqual(status["status"], "stopped")
                end_snapshot_path = Path(status["output_paths"]["cryostat_snapshot_end"])
                self.assertTrue(end_snapshot_path.exists())

                metadata = json.loads(Path(status["metadata_path"]).read_text(encoding="utf-8"))
                self.assertEqual(metadata["status"], "stopped")
                self.assertIsNotNone(metadata["final_cryostat_snapshot"])
            finally:
                await asyncio.wait_for(service.stop(), timeout=5)


class ElectricalApiTests(unittest.IsolatedAsyncioTestCase):
    def _app_for_service(self, service):
        app = create_app(config=ElectricalServiceConfig())
        app.state.electrical = service
        return app

    def test_create_app_loads_repo_config_by_default(self) -> None:
        app = create_app()
        endpoint = next(
            route.endpoint for route in app.routes if getattr(route, "path", None) == "/config"
        )
        payload = asyncio.run(endpoint())
        self.assertIn("mock_meter", payload["instruments"])
        self.assertIn("iv_mock", payload["plans"])

    async def test_health_state_and_runs_endpoints(self) -> None:
        class FakeElectricalApiService:
            def state_snapshot(self) -> dict:
                return {"status": "ok"}

            def run_status(self) -> dict:
                return {
                    "status": "idle",
                    "metadata_path": "/tmp/metadata.json",
                    "output_paths": {"jsonl": "/tmp/run.jsonl"},
                }

        app = self._app_for_service(FakeElectricalApiService())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/health")
            state = await client.get("/state")
            runs = await client.get("/runs")
            current_run = await client.get("/runs/current")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(state.status_code, 200)
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(current_run.status_code, 200)
        self.assertIn("run", runs.json())
        self.assertEqual(current_run.json()["run"]["metadata_path"], "/tmp/metadata.json")
        self.assertEqual(current_run.json()["metadata_path"], "/tmp/metadata.json")
        self.assertEqual(current_run.json()["output_paths"]["jsonl"], "/tmp/run.jsonl")

    async def test_recipe_signal_returns_400_for_unknown_plan(self) -> None:
        class FakeElectricalApiService:
            async def trigger_recipe_signal(self, signal: str, message: str | None = None) -> dict:
                raise ValueError(f"No electrical plan is configured for recipe signal {signal!r}")

        app = self._app_for_service(FakeElectricalApiService())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/plans/recipe-signal", json={"signal": "missing"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("No electrical plan", response.json()["detail"])


class ElectricalConfigValidationTests(unittest.TestCase):
    def test_timeout_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout_s must be > 0"):
            CryostatEndpointConfig(timeout_s=0)

    def test_poll_interval_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "poll_interval_s must be > 0"):
            CryostatEndpointConfig(poll_interval_s=0)

    def test_stale_after_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "stale_after_s must be > 0"):
            CryostatEndpointConfig(stale_after_s=0)

    def test_plan_steps_must_reference_existing_instruments(self) -> None:
        with self.assertRaisesRegex(ValueError, "references unknown instrument 'missing'"):
            config_from_mapping(
                {
                    "electrical": {
                        "instruments": {"mock_meter": {}},
                        "plans": [
                            {
                                "id": "bad",
                                "trigger": {"type": "recipe_signal", "signal": "measure_bad"},
                                "steps": [{"instrument": "missing", "action": "measure"}],
                            }
                        ],
                    }
                }
            )


class ElectricalCsvMeasurementWriterTests(unittest.TestCase):
    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_creates_csv_with_header_and_first_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("test run")
            writer.append_row(
                "test run",
                {
                    "run_id": "test run",
                    "plan_id": "periodic",
                    "instrument": "mock_meter",
                    "timestamp_unix_s": 1710000000.123,
                    "timestamp_iso": "2024-03-09T10:00:00.123Z",
                    "time_relative_s": 0.532,
                    "sample_temperature_K": 4.21,
                    "field_T": 1.5,
                    "safe_to_measure": True,
                    "current_A": 1.0e-6,
                },
            )

            self.assertTrue(path.exists())
            rows = self._read_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "test run")
            self.assertEqual(rows[0]["current_A"], "1e-06")

    def test_flatten_measurement_supports_nested_payloads(self) -> None:
        flattened = flatten_measurement(
            {
                "current_A": 1.0e-6,
                "source": {"voltage_V": 2.1e-3},
                "samples": [1, 2, 3],
            }
        )

        self.assertEqual(flattened["current_A"], 1.0e-6)
        self.assertEqual(flattened["source_voltage_V"], 2.1e-3)
        self.assertEqual(flattened["samples"], "[1,2,3]")

    def test_serializes_non_scalars_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("json-run")
            writer.append_row(
                "json-run",
                {
                    "run_id": "json-run",
                    "plan_id": "periodic",
                    "instrument": "mock_meter",
                    "timestamp_unix_s": 1.0,
                    "timestamp_iso": "2024-03-09T10:00:00.000Z",
                    "time_relative_s": 0.0,
                    "sample_temperature_K": None,
                    "field_T": None,
                    "safe_to_measure": True,
                    "metadata": {"range": [1, 2]},
                },
            )

            rows = self._read_rows(path)
            self.assertEqual(rows[0]["metadata"], '{"range":[1,2]}')

    def test_updates_schema_when_new_columns_appear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("schema-run")
            writer.append_row(
                "schema-run",
                {
                    "run_id": "schema-run",
                    "plan_id": "periodic",
                    "instrument": "mock_meter",
                    "timestamp_unix_s": 1.0,
                    "timestamp_iso": "2024-03-09T10:00:00.000Z",
                    "time_relative_s": 0.0,
                    "sample_temperature_K": 4.2,
                    "field_T": 1.5,
                    "safe_to_measure": True,
                    "current_A": 1e-6,
                },
            )
            writer.append_row(
                "schema-run",
                {
                    "run_id": "schema-run",
                    "plan_id": "periodic",
                    "instrument": "mock_meter",
                    "timestamp_unix_s": 2.0,
                    "timestamp_iso": "2024-03-09T10:00:01.000Z",
                    "time_relative_s": 1.0,
                    "sample_temperature_K": 4.3,
                    "field_T": 1.6,
                    "safe_to_measure": True,
                    "current_A": 2e-6,
                    "voltage_V": 3e-3,
                },
            )

            rows = self._read_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertIn("voltage_V", rows[0])
            self.assertEqual(rows[0]["voltage_V"], "")
            self.assertEqual(rows[1]["voltage_V"], "0.003")

    def test_missing_cryostat_values_leave_empty_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("missing-cryostat")
            writer.append_row(
                "missing-cryostat",
                {
                    "run_id": "missing-cryostat",
                    "plan_id": "periodic",
                    "instrument": "mock_meter",
                    "timestamp_unix_s": 1.0,
                    "timestamp_iso": "2024-03-09T10:00:00.000Z",
                    "time_relative_s": 0.0,
                    "sample_temperature_K": None,
                    "field_T": None,
                    "safe_to_measure": False,
                },
            )

            rows = self._read_rows(path)
            self.assertEqual(rows[0]["sample_temperature_K"], "")
            self.assertEqual(rows[0]["field_T"], "")

    def test_run_id_is_sanitized_and_cannot_escape_save_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("../../evil")

            self.assertTrue(path.resolve().is_relative_to(Path(tmpdir).resolve()))
            self.assertNotIn("..", path.parts)
            self.assertEqual(path.parent.name, "evil")

    def test_csv_path_is_created_inside_per_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ElectricalCsvMeasurementWriter(tmpdir)
            path = writer.begin_run("shared-dir")

            self.assertEqual(path.parent.name, "shared-dir")
            self.assertEqual(path.name, "shared-dir_electrical.csv")
