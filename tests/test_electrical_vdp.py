import asyncio
import tempfile
import unittest
from pathlib import Path

import yaml

from teslatron_services.electrical.config import (
    CryostatEndpointConfig,
    ElectricalServiceConfig,
    InstrumentConfig,
    MeasurementPlanConfig,
    MeasurementSessionConfig,
    MeasurementStepConfig,
    PlanCompletionConfig,
    PlanTriggerConfig,
    VdpConfig,
    config_from_mapping,
)
from teslatron_services.electrical.orchestrator import ElectricalMeasurementService
from teslatron_services.electrical.vdp import run_vdp_characterization_for_teslatron
from teslatron_services.electrical.vdp.scpi import SocketTransport


class ElectricalVdpConfigTests(unittest.TestCase):
    def test_config_accepts_vdp_characterization_action(self) -> None:
        config = config_from_mapping(
            {
                "electrical": {
                    "instruments": {"vdp": {}},
                    "plans": [
                        {
                            "id": "vdp",
                            "trigger": {"type": "recipe_signal", "signal": "measure_vdp"},
                            "steps": [{"instrument": "vdp", "action": "vdp_characterization"}],
                        }
                    ],
                }
            }
        )
        self.assertEqual(config.plans["vdp"].steps[0].action, "vdp_characterization")

    def test_config_rejects_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            config_from_mapping(
                {
                    "electrical": {
                        "instruments": {"x": {}},
                        "plans": [
                            {
                                "id": "bad",
                                "trigger": {"type": "recipe_signal", "signal": "measure_bad"},
                                "steps": [{"instrument": "x", "action": "unknown_action"}],
                            }
                        ],
                    }
                }
            )


class ElectricalVdpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.config_dir = self.repo_root / "config"

    def _write_vdp_fixture_files(
        self,
        tmpdir: str,
        *,
        settling_time_s: float = 0.001,
        repeats: int = 1,
        characterization_currents: list[float] | None = None,
    ) -> VdpConfig:
        tmp_path = Path(tmpdir)
        instruments_path = tmp_path / "vdp_instruments.yaml"
        sequences_path = tmp_path / "vdp_measurement_sequences.yaml"
        routing_path = tmp_path / "vdp_routing_template.yaml"
        wiring_path = tmp_path / "vdp_wiring.example.yaml"

        instruments_path.write_text(
            (self.config_dir / "vdp_instruments.example.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        routing_path.write_text(
            (self.config_dir / "vdp_routing_template.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        wiring_path.write_text(
            (self.config_dir / "vdp_wiring.example.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        sequences = yaml.safe_load(
            (self.config_dir / "vdp_measurement_sequences.yaml").read_text(encoding="utf-8")
        )
        sequences["defaults"]["settling_time_s"] = settling_time_s
        sequences["defaults"]["repeats"] = repeats
        if characterization_currents is not None:
            sequences["defaults"]["characterization_currents_A"] = characterization_currents
        sequences_path.write_text(yaml.safe_dump(sequences, sort_keys=False), encoding="utf-8")

        return VdpConfig(
            instruments_config=str(instruments_path),
            measurement_sequences_config=str(sequences_path),
            routing_config=str(routing_path),
            wiring_config=str(wiring_path),
            execute=False,
            include_contact_check=False,
            include_hall=False,
        )

    async def _make_service(
        self,
        *,
        save_dir: str,
        vdp_config: VdpConfig,
        plan: MeasurementPlanConfig,
        recipe_notifier=None,
    ) -> ElectricalMeasurementService:
        config = ElectricalServiceConfig(
            cryostat=CryostatEndpointConfig(),
            measurement_session=MeasurementSessionConfig(save_dir=save_dir),
            instruments={"vdp": InstrumentConfig()},
            vdp=vdp_config,
            plans={plan.id: plan},
        )

        async def fetch_cryostat() -> dict:
            return {
                "timestamp": 123.0,
                "temperature": {
                    "sample": {"temperature_K": 4.2},
                    "vti": {"temperature_K": 4.3},
                },
                "field": {"B_T": 0.0},
                "pressure": {"mbar": 1e-6},
                "safety": {"safe_to_measure": True},
            }

        service = ElectricalMeasurementService(
            config,
            cryostat_fetcher=fetch_cryostat,
            recipe_notifier=recipe_notifier,
        )
        await service.start()
        self.addAsyncCleanup(service.stop)
        return service

    async def test_run_vdp_characterization_for_teslatron_dry_run_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vdp_config = self._write_vdp_fixture_files(tmpdir, settling_time_s=0.0001)
            result = run_vdp_characterization_for_teslatron(
                config=vdp_config,
                run_id="dry_run_vdp",
                output_dir=Path(tmpdir) / "data",
                cryostat_snapshot_getter=lambda: {"temperature_K": 4.2},
                stop_requested=lambda: False,
            )

            self.assertEqual(result["kind"], "vdp_characterization")
            self.assertEqual(result["status"], "completed")
            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["report_json_path"]).exists())
            self.assertTrue(Path(result["report_md_path"]).exists())
            self.assertGreater(len(result["records"]), 0)
            self.assertTrue(result["sheet_resistance_ohm_per_sq"] is not None or result["flags"])

    async def test_trigger_recipe_signal_measure_vdp_completes_in_dry_run(self) -> None:
        notifications = []

        async def notify(signal: str, message: str | None) -> dict:
            notifications.append({"signal": signal, "message": message})
            return {"ok": True}

        plan = MeasurementPlanConfig(
            id="vdp_characterization",
            trigger=PlanTriggerConfig(type="recipe_signal", signal="measure_vdp"),
            steps=[MeasurementStepConfig(instrument="vdp", action="vdp_characterization")],
            completion=PlanCompletionConfig(
                notify_recipe=True,
                success_signal="measure_vdp.completed",
                failure_signal="measure_vdp.failed",
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            vdp_config = self._write_vdp_fixture_files(tmpdir, settling_time_s=0.0001)
            service = await self._make_service(
                save_dir=str(Path(tmpdir) / "electrical"),
                vdp_config=vdp_config,
                plan=plan,
                recipe_notifier=notify,
            )
            status = await service.trigger_recipe_signal("measure_vdp")
            self.assertEqual(status["status"], "running")
            await asyncio.wait_for(service._run_task, timeout=10)

            run = service.state_snapshot()["run"]
            self.assertEqual(run["status"], "completed")
            self.assertGreater(run["points_acquired"], 0)
            self.assertTrue(Path(run["output_path"]).exists())
            self.assertEqual(notifications[0]["signal"], "measure_vdp.completed")

            result = run["last_event"]
            self.assertEqual(result["kind"], "vdp_characterization")
            self.assertTrue(result["sheet_resistance_ohm_per_sq"] is not None or result["flags"])

    async def test_stop_run_interrupts_vdp_dry_run_safely(self) -> None:
        notifications = []

        async def notify(signal: str, message: str | None) -> dict:
            notifications.append({"signal": signal, "message": message})
            return {"ok": True}

        plan = MeasurementPlanConfig(
            id="vdp_characterization",
            trigger=PlanTriggerConfig(type="recipe_signal", signal="measure_vdp"),
            steps=[MeasurementStepConfig(instrument="vdp", action="vdp_characterization")],
            completion=PlanCompletionConfig(
                notify_recipe=True,
                success_signal="measure_vdp.completed",
                failure_signal="measure_vdp.failed",
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            vdp_config = self._write_vdp_fixture_files(
                tmpdir,
                settling_time_s=0.02,
                repeats=8,
                characterization_currents=[1e-5, 2e-5],
            )
            service = await self._make_service(
                save_dir=str(Path(tmpdir) / "electrical"),
                vdp_config=vdp_config,
                plan=plan,
                recipe_notifier=notify,
            )
            await service.trigger_recipe_signal("measure_vdp")
            await asyncio.sleep(0.1)
            status = await service.stop_run()

            self.assertIn(status["status"], {"aborted", "stopped"})
            self.assertEqual(notifications[-1]["signal"], "measure_vdp.failed")


class SocketTransportTests(unittest.TestCase):
    def test_close_is_idempotent(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        fake_socket = FakeSocket()
        transport = SocketTransport.__new__(SocketTransport)
        transport._socket = fake_socket

        transport.close()
        transport.close()

        self.assertIsNone(transport._socket)
        self.assertEqual(fake_socket.close_calls, 1)
