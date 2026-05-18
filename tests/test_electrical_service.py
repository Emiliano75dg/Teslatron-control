import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from teslatron_services.electrical.api import create_app
from teslatron_services.electrical.config import CryostatEndpointConfig
from teslatron_services.electrical.config import ElectricalServiceConfig
from teslatron_services.electrical.config import InstrumentConfig
from teslatron_services.electrical.config import MeasurementPlanConfig
from teslatron_services.electrical.config import MeasurementSessionConfig
from teslatron_services.electrical.config import MeasurementStepConfig
from teslatron_services.electrical.config import PlanCompletionConfig
from teslatron_services.electrical.config import PlanTriggerConfig
from teslatron_services.electrical.orchestrator import ElectricalMeasurementService


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
            measurement_session=MeasurementSessionConfig(save_dir=save_dir or "data/test-electrical"),
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


class ElectricalApiTests(unittest.TestCase):
    def test_create_app_loads_repo_config_by_default(self) -> None:
        app = create_app()
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/config")
        payload = asyncio.run(endpoint())
        self.assertIn("mock_meter", payload["instruments"])
        self.assertIn("iv_mock", payload["plans"])
