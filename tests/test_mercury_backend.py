import unittest
from unittest import mock
from types import SimpleNamespace

from teslatron_services.cryostat.backends import (
    GasControlMode,
    MercuryCryostatBackend,
    MercuryResource,
    SwitchHeaterStatus,
    _pressure_mode_from_loop_state,
    _within_tolerance,
)
from teslatron_services.cryostat.config import CryostatServiceConfig


class FakeResource:
    def __init__(self):
        self.commands = []

    def set(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        return "STAT:DEV:MOCK:SIG:SWHT:OFF\n"


class MercuryBackendBehaviorTests(unittest.TestCase):
    def make_backend(self) -> MercuryCryostatBackend:
        backend = MercuryCryostatBackend.__new__(MercuryCryostatBackend)
        backend.config = CryostatServiceConfig()
        backend.itc = FakeResource()
        backend.ips = FakeResource()
        backend._sample_target_K = None
        backend._sample_rate_K_per_min = None
        backend._vti_target_K = None
        backend._vti_rate_K_per_min = None
        backend._field_target_T = None
        backend._field_rate_T_per_min = None
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

    def test_ramp_to_zero_requires_ready_switch_heater(self) -> None:
        backend = self.make_backend()
        backend._ensure_switch_heater_ready_for_ramp = lambda: (_ for _ in ()).throw(
            PermissionError("blocked")
        )

        with self.assertRaises(PermissionError):
            backend.ramp_to_zero(0.1)

        self.assertEqual(backend.ips.commands, [])

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
