"""Hardware instrument definitions and control classes.

Defines dataclasses for:
- B2902B: Keysight precision SMU (current source, voltage meter)
- DAQ6510: Keithley data acquisition unit with 7709 multiplexer
- DryRunRunner: Simulated runner for testing without hardware

Each instrument is abstracted behind a Transport protocol for SCPI
command execution, enabling both real hardware and simulation modes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .planner import MeasurementStep
from .scpi import Transport


@dataclass
class B2902B:
    transport: Transport
    channel: int = 1

    def identify(self) -> str:
        return self.transport.query("*IDN?")

    def reset(self) -> None:
        self.transport.write("*RST")
        self.transport.query("*OPC?")

    def configure_current_source(
        self,
        *,
        compliance_v: float,
        nplc: float,
        remote_sense: bool = True,
    ) -> None:
        channel = self.channel
        self.transport.write(f":SOUR{channel}:FUNC:MODE CURR")
        self.transport.write(f":SENS{channel}:FUNC \"VOLT\",\"CURR\"")
        self.transport.write(f":SENS{channel}:VOLT:PROT {compliance_v}")
        self.transport.write(f":SENS{channel}:VOLT:NPLC {nplc}")
        self.transport.write(f":SENS{channel}:CURR:NPLC {nplc}")
        self.transport.write(f":SENS{channel}:REM {'ON' if remote_sense else 'OFF'}")
        self.set_current(0.0)
        self.output_off()

    def set_current(self, current_a: float) -> None:
        self.transport.write(f":SOUR{self.channel}:CURR {current_a:.12g}")

    def measure_voltage_current(self) -> str:
        voltage = _first_numeric_field(self.transport.query(f":MEAS:VOLT? (@{self.channel})"))
        current = _first_numeric_field(self.transport.query(f":MEAS:CURR? (@{self.channel})"))
        return f"{voltage},{current},0"

    def voltage_compliance_tripped(self) -> bool:
        response = self.transport.query(f":SENS{self.channel}:VOLT:PROT:TRIP?")
        return bool(int(float(response.strip())))

    def output_on(self) -> None:
        self.transport.write(f":OUTP{self.channel} ON")

    def output_off(self) -> None:
        self.transport.write(f":OUTP{self.channel} OFF")


@dataclass
class DAQ6510:
    transport: Transport

    def identify(self) -> str:
        return self.transport.query("*IDN?")

    def reset(self) -> None:
        self.transport.write("*RST")
        self.transport.query("*OPC?")

    def ensure_scpi(self) -> None:
        language = self.transport.query("*LANG?").strip().upper()
        if language != "SCPI":
            raise RuntimeError(f"DAQ6510 command set must be SCPI, got {language!r}")

    def open_all(self) -> None:
        self.transport.write(":ROUT:OPEN:ALL")
        self.transport.query("*OPC?")

    def close_channels(self, channels: tuple[int, ...]) -> None:
        channel_list = ",".join(str(channel) for channel in channels)
        self.transport.write(f":ROUT:CLOS (@{channel_list})")
        self.transport.query("*OPC?")


def _first_numeric_field(response: str) -> str:
    return response.strip().split(",", 1)[0].strip()


@dataclass
class DryRunRunner:
    b2902b: B2902B
    daq6510: DAQ6510
    settling_time_s: float

    def prepare(self, *, compliance_v: float, nplc: float, remote_sense: bool) -> None:
        self.daq6510.identify()
        self.b2902b.identify()
        self.daq6510.ensure_scpi()
        self.daq6510.open_all()
        self.b2902b.configure_current_source(
            compliance_v=compliance_v,
            nplc=nplc,
            remote_sense=remote_sense,
        )

    def run_step_commands(self, step: MeasurementStep) -> None:
        if not isinstance(step.current_a, float):
            raise ValueError("dry-run command expansion currently expects a numeric current")
        self.b2902b.set_current(0.0)
        self.b2902b.output_off()
        self.daq6510.open_all()
        self.daq6510.close_channels(step.relay_closures)
        self.b2902b.output_on()
        self.b2902b.set_current(step.current_a)
        self.b2902b.transport.write(f"# wait {self.settling_time_s:.6g} s")
        self.b2902b.measure_voltage_current()
        self.b2902b.voltage_compliance_tripped()
        self.b2902b.set_current(-step.current_a)
        self.b2902b.transport.write(f"# wait {self.settling_time_s:.6g} s")
        self.b2902b.measure_voltage_current()
        self.b2902b.voltage_compliance_tripped()
        self.b2902b.set_current(0.0)
        self.b2902b.output_off()
        self.daq6510.open_all()

    def finish(self) -> None:
        self.b2902b.set_current(0.0)
        self.b2902b.output_off()
        self.daq6510.open_all()
