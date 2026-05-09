import unittest

from teslatron_services.cryostat.config import CryostatServiceConfig
from teslatron_services.cryostat.config import InsertCapabilitiesConfig
from teslatron_services.cryostat.config import InsertProfileConfig
from teslatron_services.cryostat.config import MercurySensorSetupConfig
from teslatron_services.cryostat.config import config_from_mapping
from teslatron_services.cryostat.service import CryostatService
from teslatron_services.cryostat.state import CryostatState


class FakeBackend:
    def __init__(self):
        self.calls = []

    def close(self) -> None:
        return None

    def read_state(self) -> CryostatState:
        return CryostatState()

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


class CryostatServiceCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, *, capabilities: InsertCapabilitiesConfig | None = None) -> CryostatService:
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
        service = self.make_service(
            capabilities=InsertCapabilitiesConfig(field_control=False)
        )

        with self.assertRaises(PermissionError):
            await service.ramp_field(1.0, 0.1)

        self.assertEqual(service.backend.calls, [])

    async def test_vti_loop_blocked_for_both_temperature_command(self) -> None:
        service = self.make_service(
            capabilities=InsertCapabilitiesConfig(vti_loop=False)
        )

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


class CryostatConfigTests(unittest.TestCase):
    def test_default_config_builds_fallback_insert_profile(self) -> None:
        config = config_from_mapping({})

        self.assertEqual(config.active_insert, "fisher_probe")
        self.assertIn("fisher_probe", config.insert_profiles)
