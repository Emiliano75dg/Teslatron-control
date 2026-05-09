import unittest
from unittest import mock

from teslatron_services.cryostat.backends import (
    GasControlMode,
    MercuryCryostatBackend,
    MercuryResource,
    SwitchHeaterStatus,
    _field_rate_with_low_field_cap,
    _pressure_mode_from_loop_state,
    _within_tolerance,
)
from teslatron_services.cryostat.config import CryostatServiceConfig
from teslatron_services.cryostat.config import MercurySensorSetupConfig


class FakeResource:
    def __init__(self):
        self.commands = []
        self.responses = {}

    def set(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command in self.responses:
            return self.responses[command]
        return "STAT:DEV:MOCK:SIG:SWHT:OFF\n"


class MercuryBackendBehaviorTests(unittest.TestCase):
    def make_backend(self) -> MercuryCryostatBackend:
        backend = MercuryCryostatBackend.__new__(MercuryCryostatBackend)
        backend.config = CryostatServiceConfig()
        backend.config.ips.command_delay_s = 0.0
        backend.itc = FakeResource()
        backend.ips = FakeResource()
        backend._sample_target_K = None
        backend._sample_rate_K_per_min = None
        backend._vti_target_K = None
        backend._vti_rate_K_per_min = None
        backend._field_target_T = None
        backend._field_rate_T_per_min = None
        backend._field_requested_rate_T_per_min = None
        backend._switch_heater_target = SwitchHeaterStatus.UNKNOWN
        backend._switch_heater_changed_at = None
        backend._mode = None
        backend._aborted = False
        return backend

    def test_set_vti_needle_disables_pressure_loop_before_setting_flow(self) -> None:
        backend = self.make_backend()

        backend.set_vti_needle(12.5)

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB5.P1:PRES:LOOP:ENAB:OFF",
                "SET:DEV:DB5.P1:PRES:LOOP:FSET:12.5",
            ],
        )

    def test_ramp_field_requires_ready_switch_heater(self) -> None:
        backend = self.make_backend()
        backend._ensure_switch_heater_ready_for_ramp = lambda: (_ for _ in ()).throw(
            PermissionError("blocked")
        )

        with self.assertRaises(PermissionError):
            backend.ramp_field(1.0, 0.1)

        self.assertEqual(backend.ips.commands, [])

    def test_ramp_field_caps_rate_inside_low_field_window(self) -> None:
        backend = self.make_backend()
        backend._ensure_switch_heater_ready_for_ramp = lambda: None
        backend._read_ips_float = lambda command: 0.5 if command.endswith("SIG:FLD?") else None

        backend.ramp_field(2.0, 0.3)

        self.assertEqual(
            backend.ips.commands,
            [
                "SET:DEV:GRPZ:PSU:ACTN:HOLD",
                "SET:DEV:GRPZ:PSU:SIG:RFST:0.15",
                "SET:DEV:GRPZ:PSU:SIG:FSET:2",
                "SET:DEV:GRPZ:PSU:ACTN:RTOS",
            ],
        )
        self.assertEqual(backend._field_requested_rate_T_per_min, 0.3)
        self.assertEqual(backend._field_rate_T_per_min, 0.15)

    def test_ramp_to_zero_requires_ready_switch_heater(self) -> None:
        backend = self.make_backend()
        backend._ensure_switch_heater_ready_for_ramp = lambda: (_ for _ in ()).throw(
            PermissionError("blocked")
        )

        with self.assertRaises(PermissionError):
            backend.ramp_to_zero(0.1)

        self.assertEqual(backend.ips.commands, [])

    def test_clamp_requires_output_current_below_one_amp(self) -> None:
        backend = self.make_backend()
        backend.ips.responses["READ:DEV:GRPZ:PSU:SIG:CURR?"] = (
            "STAT:DEV:GRPZ:PSU:SIG:CURR:1.2000A\n"
        )

        with self.assertRaises(PermissionError):
            backend.clamp()

        self.assertEqual(backend.ips.commands, ["READ:DEV:GRPZ:PSU:SIG:CURR?"])

    def test_clamp_sends_clmp_when_output_current_is_safe(self) -> None:
        backend = self.make_backend()
        backend.ips.responses["READ:DEV:GRPZ:PSU:SIG:CURR?"] = (
            "STAT:DEV:GRPZ:PSU:SIG:CURR:0.2000A\n"
        )

        backend.clamp()

        self.assertEqual(
            backend.ips.commands,
            [
                "READ:DEV:GRPZ:PSU:SIG:CURR?",
                "SET:DEV:GRPZ:PSU:ACTN:CLMP",
            ],
        )

    def test_fixed_heater_disables_pid_loop_before_setting_heater_output(self) -> None:
        backend = self.make_backend()

        backend.set_temperature_fixed_heater("sample", 12.5)

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB8.T1:TEMP:LOOP:RENA:OFF",
                "SET:DEV:DB8.T1:TEMP:LOOP:ENAB:OFF",
                "SET:DEV:DB8.T1:TEMP:LOOP:HSET:12.5",
            ],
        )

    def test_fixed_target_disables_ramp_and_enables_temperature_loop(self) -> None:
        backend = self.make_backend()

        backend.set_temperature_target(42.0, loop="sample")

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB8.T1:TEMP:LOOP:RENA:OFF",
                "SET:DEV:DB8.T1:TEMP:LOOP:TSET:42",
                "SET:DEV:DB8.T1:TEMP:LOOP:ENAB:ON",
            ],
        )
        self.assertEqual(backend._sample_target_K, 42.0)

    def test_manual_pid_writes_terms_and_enables_loop(self) -> None:
        backend = self.make_backend()

        backend.set_temperature_pid("vti", p=25.0, i=1.0, d=0.0, auto=False)

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:MB1.T1:TEMP:LOOP:PIDT:OFF",
                "SET:DEV:MB1.T1:TEMP:LOOP:P:25",
                "SET:DEV:MB1.T1:TEMP:LOOP:I:1",
                "SET:DEV:MB1.T1:TEMP:LOOP:D:0",
                "SET:DEV:MB1.T1:TEMP:LOOP:ENAB:ON",
            ],
        )

    def test_auto_pid_uses_pid_table_without_overwriting_terms(self) -> None:
        backend = self.make_backend()

        backend.set_temperature_pid("sample", p=10.0, i=1.0, d=0.0, auto=True)

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB8.T1:TEMP:LOOP:PIDT:ON",
                "SET:DEV:DB8.T1:TEMP:LOOP:ENAB:ON",
            ],
        )

    def test_ramp_temperature_both_sets_vti_target_ten_percent_lower(self) -> None:
        backend = self.make_backend()

        backend.ramp_temperature(10.0, 1.0, loop="both")

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB8.T1:TEMP:LOOP:RENA:ON",
                "SET:DEV:DB8.T1:TEMP:LOOP:RSET:1",
                "SET:DEV:DB8.T1:TEMP:LOOP:TSET:10",
                "SET:DEV:DB8.T1:TEMP:LOOP:ENAB:ON",
                "SET:DEV:MB1.T1:TEMP:LOOP:RENA:ON",
                "SET:DEV:MB1.T1:TEMP:LOOP:RSET:1",
                "SET:DEV:MB1.T1:TEMP:LOOP:TSET:9",
                "SET:DEV:MB1.T1:TEMP:LOOP:ENAB:ON",
            ],
        )
        self.assertEqual(backend._sample_target_K, 10.0)
        self.assertEqual(backend._vti_target_K, 9.0)

    def test_switch_heater_ready_blocks_until_delay_elapses(self) -> None:
        backend = self.make_backend()
        backend._switch_heater_target = SwitchHeaterStatus.ON
        backend._switch_heater_changed_at = 100.0
        backend.config.ips.switch_on_delay_s = 300.0
        backend._read_switch_heater_status = lambda: SwitchHeaterStatus.ON

        with mock.patch(
            "teslatron_services.cryostat.backends.unix_time",
            return_value=250.0,
        ):
            with self.assertRaises(PermissionError):
                backend._ensure_switch_heater_ready_for_ramp()

        with mock.patch(
            "teslatron_services.cryostat.backends.unix_time",
            return_value=450.0,
        ):
            backend._ensure_switch_heater_ready_for_ramp()

    def test_maybe_adjust_field_rate_restores_requested_rate_outside_window(self) -> None:
        backend = self.make_backend()
        backend._field_requested_rate_T_per_min = 0.3
        backend._field_rate_T_per_min = 0.15

        backend._maybe_adjust_field_rate(
            field_T=1.2,
            field_rate_T_per_min=0.15,
            field_ramping=True,
        )

        self.assertEqual(
            backend.ips.commands,
            ["SET:DEV:GRPZ:PSU:SIG:RFST:0.3"],
        )
        self.assertEqual(backend._field_rate_T_per_min, 0.3)

    def test_read_state_does_not_adjust_field_rate(self) -> None:
        backend = self.make_backend()

        def fail_if_called(*args, **kwargs):
            raise AssertionError("read_state must not write field rate")

        backend._maybe_adjust_field_rate = fail_if_called

        backend.read_state()

    def test_apply_sample_sensor_writes_full_sensor_setup(self) -> None:
        backend = self.make_backend()

        backend.apply_sample_sensor(
            MercurySensorSetupConfig(
                sensor_type="CERNOX",
                excitation_type="CURR",
                excitation_magnitude="10uA",
                calibration="X205007",
            )
        )

        self.assertEqual(
            backend.itc.commands,
            [
                "SET:DEV:DB8.T1:TEMP:TYPE:CERNOX:EXCT:TYPE:CURR:MAG:10uA:CALB:X205007:DAT",
            ],
        )

    def test_apply_sample_sensor_rejects_incomplete_setup(self) -> None:
        backend = self.make_backend()

        with self.assertRaises(ValueError):
            backend.apply_sample_sensor(
                MercurySensorSetupConfig(
                    sensor_type="",
                    excitation_type="CURR",
                    excitation_magnitude="10uA",
                    calibration="X205007",
                )
            )


class MercuryBackendHelperTests(unittest.TestCase):
    def test_within_tolerance_returns_unknown_when_values_missing(self) -> None:
        self.assertIsNone(_within_tolerance(None, 1.0, 0.1))
        self.assertIsNone(_within_tolerance(1.0, None, 0.1))

    def test_pressure_mode_prefers_loop_state(self) -> None:
        self.assertEqual(
            _pressure_mode_from_loop_state(True, None, None),
            GasControlMode.PRESSURE_CONTROL,
        )
        self.assertEqual(
            _pressure_mode_from_loop_state(False, 5.0, 10.0),
            GasControlMode.FIXED_NEEDLE,
        )
        self.assertEqual(
            _pressure_mode_from_loop_state(None, 5.0, None),
            GasControlMode.PRESSURE_CONTROL,
        )
        self.assertEqual(
            _pressure_mode_from_loop_state(None, None, 10.0),
            GasControlMode.FIXED_NEEDLE,
        )

    def test_low_field_rate_cap(self) -> None:
        self.assertEqual(_field_rate_with_low_field_cap(0.0, 0.3), 0.15)
        self.assertEqual(_field_rate_with_low_field_cap(0.8, 0.1), 0.1)
        self.assertEqual(_field_rate_with_low_field_cap(1.2, 0.3), 0.3)
        self.assertEqual(_field_rate_with_low_field_cap(None, 0.3), 0.15)


class MercuryResourceSocketTests(unittest.TestCase):
    def test_socket_query_handles_split_terminator(self) -> None:
        resource = MercuryResource.__new__(MercuryResource)
        resource.timeout_ms = 3000
        resource.write_termination = "\n"
        resource.read_termination = "\r\n"
        resource._last_socket_query_at = 0.0
        resource._socket = lambda: fake_socket

        class FakeSocket:
            def __init__(self):
                self.sent = []
                self.responses = [b"STAT:DEV:TEST:OK\r", b"\n"]

            def settimeout(self, timeout: float) -> None:
                self.timeout = timeout

            def sendall(self, payload: bytes) -> None:
                self.sent.append(payload)

            def recv(self, size: int) -> bytes:
                return self.responses.pop(0)

        fake_socket = FakeSocket()

        response = MercuryResource._socket_query_once(resource, "READ:TEST")

        self.assertEqual(response, "STAT:DEV:TEST:OK\r\n")
        self.assertEqual(fake_socket.sent, [b"READ:TEST\n"])


if __name__ == "__main__":
    unittest.main()
