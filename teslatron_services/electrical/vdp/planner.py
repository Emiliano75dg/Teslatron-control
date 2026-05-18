"""Convert YAML measurement sequences to ordered measurement steps.

Key function:
- build_plan(): Takes measurement_sequences.yaml and routing_template.yaml,
  expands contact pairs and current levels, computes relay closures for
  4-point Kelvin sensing, and returns ordered MeasurementStep list.

The planner handles:
- Contact pair expansion (contact_check has 6 pairs: AB, AC, AD, BC, BD, CD)
- Current reversal (positive and negative for each pair)
- Repeat counts (typically 3 repeats per configuration)
- Relay routing matrix mapping contacts to multiplexer channels
- Measurement step sequencing (open relays, route, apply current, measure, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MeasurementStep:
    sequence_name: str
    measurement_id: str
    mode: str
    current_pair: tuple[str, str]
    voltage_pair: tuple[str, str]
    current_a: float | str
    repeat_index: int
    relay_closures: tuple[int, ...]


def build_plan(
    measurement_config: dict[str, Any],
    routing_config: dict[str, Any],
    *,
    include_hall: bool = False,
) -> list[MeasurementStep]:
    """Build executable measurement steps with numeric 7709 crosspoints.
    
    Converts YAML measurement sequences to ordered MeasurementStep list:
    1. Expand contact pairs (e.g., contact_check has 6 pairs: AB, AC, AD, BC, BD, CD)
    2. Expand current levels (positive and negative for each pair)
    3. Expand repeats (typically 3 times per configuration)
    4. Compute relay closures mapping contacts to multiplexer channels
    5. Return sorted list ready for hardware execution
    
    Args:
        measurement_config: Parsed measurement_sequences.yaml dict with:
            - defaults: settling_time_s, repeats, currents lists
            - sequences.contact_check: pairs and measurement parameters
            - sequences.full_characterization: blocks with multiple modes
        routing_config: Parsed routing_template.yaml dict with:
            - matrix: dimensions, daq_slot, channel formulas
            - sample_columns: contact-to-column mapping (A:1, B:2, ...)
            - single_channel_kelvin_rows: force/sense row assignments
        include_hall: If True, include Hall effect measurement steps (optional).
        
    Returns:
        List of MeasurementStep objects, each with:
        - sequence_name, measurement_id, mode, current/voltage pairs
        - current_a: numeric current value (or field strength for Hall)
        - repeat_index: which repeat (0, 1, 2, ...)
        - relay_closures: tuple of DAQ channel numbers to close
        
    Raises:
        KeyError: If required config keys are missing.
        ValueError: If measurement mode is not recognized.
    """
    defaults = measurement_config["defaults"]
    repeats = int(defaults["repeats"])
    plan: list[MeasurementStep] = []

    contact_sequence = measurement_config["sequences"]["contact_check"]
    contact_currents = defaults[contact_sequence["currents_ref"]]
    for measurement in contact_sequence["pair_measurements"]:
        force_pair = _pair(measurement["force_pair"])
        sense_pair = _pair(measurement["sense_pair"])
        plan.extend(
            _steps_for_measurement(
                sequence_name="contact_check",
                measurement_id=measurement["id"],
                mode=contact_sequence["mode"],
                current_pair=force_pair,
                voltage_pair=sense_pair,
                currents=contact_currents,
                repeats=repeats,
                routing_config=routing_config,
            )
        )

    full = measurement_config["sequences"]["full_characterization"]
    characterization_currents = defaults[full["currents_ref"]]
    for block_name, block in full["blocks"].items():
        if block_name in {"anisotropy_nonuniformity"}:
            continue
        if block_name == "hall" and not include_hall:
            continue
        for measurement in _iter_block_measurements(block):
            current_pair = _pair(measurement["current_pair"])
            voltage_pair = _pair(measurement["voltage_pair"])
            currents = block.get("field_points_T") or characterization_currents
            if block_name == "hall":
                # Hall steps are expanded by field point and current value in the real runner.
                # The plan records the field point in current_a for now, keeping routing explicit.
                currents = block["field_points_T"]
            plan.extend(
                _steps_for_measurement(
                    sequence_name=f"full_characterization.{block_name}",
                    measurement_id=measurement["id"],
                    mode=block["mode"],
                    current_pair=current_pair,
                    voltage_pair=voltage_pair,
                    currents=currents,
                    repeats=repeats,
                    routing_config=routing_config,
                )
            )

    return plan


def relay_closures(
    routing_config: dict[str, Any],
    *,
    mode: str,
    current_pair: tuple[str, str],
    voltage_pair: tuple[str, str],
) -> tuple[int, ...]:
    """Compute relay closures for a measurement configuration.
    
    Maps logical contact pairs (force, sense) to physical relay channels
    in the Keithley 7709 multiplexer via the routing matrix.
    
    For mode='kelvin_local_single_channel':
    - Force current through contacts: current_pair[0] -> force_hi, current_pair[1] -> force_lo
    - Sense voltage between contacts: voltage_pair[0] -> sense_hi, voltage_pair[1] -> sense_lo
    
    The routing template maps:
    - Contacts (A, B, C, D) -> sample columns (typically 1, 2, 3, 4)
    - Row assignments (force_hi, force_lo, sense_hi, sense_lo) -> matrix rows (typically 1, 2, 3, 4)
    - Crosspoint formula computes matrix crosspoint from (row, column)
    - DAQ channel formula computes global DAQ channel from (slot, crosspoint)
    
    Args:
        routing_config: routing_template.yaml dict with matrix, sample_columns, rows.
        mode: Measurement mode ('kelvin_local_single_channel', 'nonlocal_four_terminal', etc.).
        current_pair: Tuple of contact names (e.g., ('A', 'B')) to force current.
        voltage_pair: Tuple of contact names (e.g., ('A', 'B')) to sense voltage.
        
    Returns:
        Tuple of DAQ channel indices to close for this configuration.
        Example: (101, 102, 103, 104) for 4-contact Kelvin measurement.
        
    Raises:
        ValueError: If mode is not recognized in routing_config.
        KeyError: If required routing config fields are missing.
    """
    sample_columns = routing_config["sample_columns"]

    # Map logical rows/columns based on measurement mode
    # In kelvin_local_single_channel: force current through one pair, sense voltage across same pair
    if mode == "kelvin_local_single_channel":
        rows = routing_config["single_channel_kelvin_rows"]
        # Logical mapping: (row_name, contact_to_route)
        # Example: force current from A through force_hi, and to B through force_lo
        logical = [
            ("force_hi", current_pair[0]),
            ("force_lo", current_pair[1]),
            ("sense_hi", voltage_pair[0]),
            ("sense_lo", voltage_pair[1]),
        ]
    elif mode == "nonlocal_four_terminal":
        rows = routing_config["single_channel_kelvin_rows"]
        logical = [
            ("force_hi", current_pair[0]),
            ("force_lo", current_pair[1]),
            ("sense_hi", voltage_pair[0]),
            ("sense_lo", voltage_pair[1]),
        ]
    elif mode == "nonlocal_four_terminal_with_field":
        rows = routing_config["single_channel_kelvin_rows"]
        logical = [
            ("force_hi", current_pair[0]),
            ("force_lo", current_pair[1]),
            ("sense_hi", voltage_pair[0]),
            ("sense_lo", voltage_pair[1]),
        ]
    else:
        raise ValueError(f"unsupported routing mode: {mode}")

    # Convert logical routing to physical DAQ channel numbers
    # Each closure specifies: slot*100 + crosspoint where crosspoint = (row-1)*8 + column
    slot = int(routing_config["matrix"].get("daq_slot", 1))
    return tuple(
        _daq_channel(slot=slot, crosspoint=_crosspoint(row=rows[row_name], column=sample_columns[contact]))
        for row_name, contact in logical
    )


def _steps_for_measurement(
    *,
    sequence_name: str,
    measurement_id: str,
    mode: str,
    current_pair: tuple[str, str],
    voltage_pair: tuple[str, str],
    currents: list[float | str],
    repeats: int,
    routing_config: dict[str, Any],
) -> list[MeasurementStep]:
    closures = relay_closures(
        routing_config,
        mode=mode,
        current_pair=current_pair,
        voltage_pair=voltage_pair,
    )
    return [
        MeasurementStep(
            sequence_name=sequence_name,
            measurement_id=measurement_id,
            mode=mode,
            current_pair=current_pair,
            voltage_pair=voltage_pair,
            current_a=current,
            repeat_index=repeat_index,
            relay_closures=closures,
        )
        for current in currents
        for repeat_index in range(1, repeats + 1)
    ]


def _iter_block_measurements(block: dict[str, Any]) -> list[dict[str, Any]]:
    if block.get("measurements") is not None:
        return list(block["measurements"])
    if block.get("measurement_pairs") is None:
        return []

    measurements: list[dict[str, Any]] = []
    for pair in block["measurement_pairs"]:
        for side in ("forward", "reciprocal"):
            item = pair[side]
            if isinstance(item, str):
                continue
            measurements.append(item)
    return measurements


def _pair(value: list[str]) -> tuple[str, str]:
    if len(value) != 2:
        raise ValueError(f"expected a pair, got {value}")
    return value[0], value[1]


def _crosspoint(*, row: int, column: int) -> int:
    """Compute Keithley 7709 matrix crosspoint from (row, column).
    
    The 7709 has a 6x8 matrix, indexed from 1.
    Crosspoint formula: (row - 1) * 8 + column
    Maps (1,1)→1, (1,8)→8, (2,1)→9, ..., (6,8)→48
    """
    return (row - 1) * 8 + column


def _daq_channel(*, slot: int, crosspoint: int) -> int:
    """Compute global DAQ channel from multiplexer slot and crosspoint.
    
    The DAQ6510 can have multiple switching modules in different slots.
    DAQ channel formula: slot * 100 + crosspoint
    Maps slot1→100-148, slot2→200-248, slot3→300-348
    """
    return slot * 100 + crosspoint
