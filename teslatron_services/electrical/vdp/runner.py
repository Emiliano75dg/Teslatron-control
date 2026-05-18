"""Execute measurement sequences by sending SCPI commands to instruments.

Key classes:
- ContactCheckRunner: Executes contact-check measurements with relay routing
- MeasurementRecord: Dataclass for a single measurement (current, voltage, pair)

Workflow:
1. prepare() - Set up instrument channels and compliance
2. For each MeasurementStep:
   - Close relays for current path via multiplexer
   - Apply positive current, measure voltage
   - Apply negative current, measure voltage
   - Extract odd-resistance (used for R calculation)
3. finish() - Open all relays to safe state

Handles timestamp, relay control, current source, voltage measurement,
and accumulation of results to CSV file.
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .analysis import odd_resistance
from .instruments import B2902B, DAQ6510
from .planner import MeasurementStep


@dataclass(frozen=True)
class MeasurementRecord:
    """A single measurement result with current, voltage, and instrument status."""

    sequence_name: str
    measurement_id: str
    mode: str
    current_pair: str
    voltage_pair: str
    current_set_A: float
    current_measured_positive_A: float
    current_measured_negative_A: float
    voltage_positive_V: float
    voltage_negative_V: float
    resistance_ohm: float
    status_positive: int
    status_negative: int
    compliance_positive: bool
    compliance_negative: bool
    settling_time_s: float
    repeat_index: int
    timestamp: str
    relay_closures: str


class MeasurementCancelled(RuntimeError):
    """Raised when the user requests a safe measurement stop."""


class ContactCheckRunner:
    """Execute contact-check measurements via relay routing and SCPI commands.

    Manages the full measurement lifecycle:
    - prepare(): Configure instruments, open all relays
    - run(): Execute list of MeasurementSteps, write CSV
    - finish(): Turn off output, open all relays (cleanup)
    """

    def __init__(
        self,
        *,
        b2902b: B2902B,
        daq6510: DAQ6510,
        settling_time_s: float,
        compliance_v: float,
        nplc: float,
        remote_sense: bool,
        sleep_enabled: bool = True,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize runner with instruments and measurement parameters.

        Args:
            b2902b: Keysight SMU for current source and voltage measurement.
            daq6510: Keithley DAQ with 7709 multiplexer for relay control.
            settling_time_s: Wait time after relay change (float, seconds).
            compliance_v: Voltage compliance limit (V) to protect samples.
            nplc: Number of Power Line Cycles for voltage integration.
            remote_sense: Use remote sense leads for accurate measurement.
            sleep_enabled: If False, skip settling delays (for testing).
        """
        self.b2902b = b2902b
        self.daq6510 = daq6510
        self.settling_time_s = settling_time_s
        self.compliance_v = compliance_v
        self.nplc = nplc
        self.remote_sense = remote_sense
        self.sleep_enabled = sleep_enabled
        self.stop_requested = stop_requested or (lambda: False)

    def prepare(self) -> None:
        """Initialize instruments for measurement.

        - Queries B2902B and DAQ6510 identification
        - Sets DAQ to SCPI command language
        - Opens all relays to safe state
        - Configures B2902B as current source with compliance voltage

        Call this once before running measurements.
        """
        self.daq6510.identify()
        self.b2902b.identify()
        self.daq6510.ensure_scpi()
        self.daq6510.open_all()
        self.b2902b.configure_current_source(
            compliance_v=self.compliance_v,
            nplc=self.nplc,
            remote_sense=self.remote_sense,
        )

    def run(self, steps: Iterable[MeasurementStep], output_path: Path) -> list[MeasurementRecord]:
        """Execute measurement steps and write results to CSV.

        For each MeasurementStep:
        1. Close relays to route current through force pair
        2. Set positive current, measure voltage
        3. Set negative current, measure voltage
        4. Extract odd-in-current resistance
        5. Accumulate MeasurementRecord to results

        All relays opened in finally block for safe cleanup even on error.

        Args:
            steps: Iterable of MeasurementStep objects defining measurements.
            output_path: Path to write CSV file with results.

        Returns:
            List of MeasurementRecord objects (also written to CSV).
        """
        records: list[MeasurementRecord] = []
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer: csv.DictWriter[str] | None = None
            try:
                self._raise_if_stopped()
                self.prepare()
                for step in steps:
                    self._raise_if_stopped()
                    record = self.run_step(step)
                    records.append(record)
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=list(asdict(record)))
                        writer.writeheader()
                    writer.writerow(asdict(record))
                    handle.flush()
            finally:
                self.finish()
        return records

    def run_step(self, step: MeasurementStep) -> MeasurementRecord:
        """Execute a single measurement step.

        1. Close relays for current path (force pair) via multiplexer
        2. Apply positive current, measure voltage/current/status
        3. Apply negative current, measure voltage/current/status
        4. Extract odd-in-current resistance from measured values
        5. Return MeasurementRecord with all data and metadata

        Args:
            step: MeasurementStep defining current pair, routing, and current value.

        Returns:
            MeasurementRecord with measurement results and status.

        Raises:
            ValueError: If step.current_a is not numeric.
        """
        if not isinstance(step.current_a, float):
            raise ValueError("contact check expects numeric current values")

        self._raise_if_stopped()
        self._open_all_safely()
        self._raise_if_stopped()
        self.daq6510.close_channels(step.relay_closures)
        self.b2902b.output_on()

        self._raise_if_stopped()
        self.b2902b.set_current(step.current_a)
        self._sleep()
        self._raise_if_stopped()
        positive = parse_b2902b_fetch(self.b2902b.measure_voltage_current())
        compliance_positive = self.b2902b.voltage_compliance_tripped()

        self._raise_if_stopped()
        self.b2902b.set_current(-step.current_a)
        self._sleep()
        self._raise_if_stopped()
        negative = parse_b2902b_fetch(self.b2902b.measure_voltage_current())
        compliance_negative = self.b2902b.voltage_compliance_tripped()
        positive_current_a = _current_with_setpoint_sign(
            measured_current_a=positive.current_a,
            setpoint_current_a=step.current_a,
        )
        negative_current_a = _current_with_setpoint_sign(
            measured_current_a=negative.current_a,
            setpoint_current_a=-step.current_a,
        )

        resistance = odd_resistance(
            positive.voltage_v,
            negative.voltage_v,
            positive_current_a,
            negative_current_a,
        )

        self._open_all_safely()
        return MeasurementRecord(
            sequence_name=step.sequence_name,
            measurement_id=step.measurement_id,
            mode=step.mode,
            current_pair="-".join(step.current_pair),
            voltage_pair="-".join(step.voltage_pair),
            current_set_A=step.current_a,
            current_measured_positive_A=positive_current_a,
            current_measured_negative_A=negative_current_a,
            voltage_positive_V=positive.voltage_v,
            voltage_negative_V=negative.voltage_v,
            resistance_ohm=resistance,
            status_positive=positive.status,
            status_negative=negative.status,
            compliance_positive=positive.compliance or compliance_positive,
            compliance_negative=negative.compliance or compliance_negative,
            settling_time_s=self.settling_time_s,
            repeat_index=step.repeat_index,
            timestamp=datetime.now(timezone.utc).isoformat(),
            relay_closures=",".join(str(channel) for channel in step.relay_closures),
        )

    def finish(self) -> None:
        """Clean up instruments to safe state.

        - Turn off B2902B current source output
        - Open all relays in DAQ6510 multiplexer

        Always called in finally block after run() for safe cleanup.
        """
        self.b2902b.set_current(0.0)
        self._sleep(check_stop=False)
        self.b2902b.output_off()
        self.daq6510.open_all()

    def _sleep(self, *, check_stop: bool = True) -> None:
        if self.sleep_enabled:
            deadline = time.monotonic() + self.settling_time_s
            while time.monotonic() < deadline:
                if check_stop:
                    self._raise_if_stopped()
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    break
                time.sleep(min(0.1, remaining_s))
            if check_stop:
                self._raise_if_stopped()

    def _raise_if_stopped(self) -> None:
        if self.stop_requested():
            raise MeasurementCancelled("measurement stopped by user")

    def _open_all_safely(self) -> None:
        self.b2902b.set_current(0.0)
        self._sleep(check_stop=False)
        self.b2902b.output_off()
        self.daq6510.open_all()


@dataclass(frozen=True)
class B2902BReading:
    voltage_v: float
    current_a: float
    status: int
    compliance: bool


def parse_b2902b_fetch(response: str) -> B2902BReading:
    """Parse B2902B source/measure response into structured reading.

    Response format: \"V,I,CH,STAT\" where:
    - V: voltage (float, volts)
    - I: current (float, amps)
    - CH: channel (int, usually 1 or 2)
    - STAT: status bits (int, bit 0 = compliance reached)

    Args:
        response: Raw SCPI response string from a READ?/FETCH? query.

    Returns:
        B2902BReading with voltage, current, status, and compliance flag.

    Raises:
        ValueError: If response format is invalid or cannot be parsed.
    """
    parts = [part.strip() for part in response.strip().split(",") if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"expected at least voltage,current from B2902B, got {response!r}")

    voltage_v = float(parts[0])
    current_a = float(parts[1])
    status = int(float(parts[2])) if len(parts) >= 3 else 0
    return B2902BReading(
        voltage_v=voltage_v,
        current_a=current_a,
        status=status,
        compliance=_status_indicates_compliance(status),
    )


def _current_with_setpoint_sign(*, measured_current_a: float, setpoint_current_a: float) -> float:
    if measured_current_a == 0:
        return setpoint_current_a
    sign = 1.0 if setpoint_current_a >= 0 else -1.0
    return sign * abs(measured_current_a)


def contact_check_steps(plan: Iterable[MeasurementStep]) -> list[MeasurementStep]:
    return [step for step in plan if step.sequence_name == "contact_check"]


def characterization_steps(plan: Iterable[MeasurementStep]) -> list[MeasurementStep]:
    return [
        step
        for step in plan
        if step.sequence_name.startswith("full_characterization.")
        and step.sequence_name != "full_characterization.hall"
    ]


def _status_indicates_compliance(status: int) -> bool:
    # Conservative placeholder until the B2902B status bit mapping is finalized.
    return status != 0
