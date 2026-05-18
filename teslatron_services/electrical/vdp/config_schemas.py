"""Configuration schema models using Pydantic for YAML validation.

This module defines the schema for:
- instruments.yaml: Hardware specifications (SMU, DAQ, multiplexer)
- measurement_sequences.yaml: Test sequences (contact pairs, current levels, steps)
- routing_template.yaml: Relay matrix mapping and routing rules

All models include field validators to catch configuration errors early,
before expensive hardware measurements are attempted.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_CONTACTS = {"A", "B", "C", "D"}


# ============================================================================
# Instruments Configuration Schema
# ============================================================================


class TransportConfig(BaseModel):
    """SCPI transport configuration (socket-based)."""

    default_timeout_s: float = Field(gt=0, description="Socket timeout in seconds")
    command_termination: str = Field(default="\n", description="Line terminator for SCPI commands")
    read_buffer_bytes: int = Field(default=65536, gt=0, description="Socket recv buffer size")


class B2902BConfig(BaseModel):
    """Keysight B2902B Precision SMU configuration."""

    model: Literal["Keysight_B2902B"] = "Keysight_B2902B"
    ip_address: str = Field(description="IP address for network connection")
    port: int = Field(ge=1, le=65535, description="TCP port number")
    visa_resource: str = Field(description="VISA resource string")
    channel: int = Field(ge=1, le=2, description="Channel number (1 or 2)")
    line_frequency_Hz: float = Field(default=50, description="Line frequency for filtering")
    nplc: float = Field(gt=0, le=100, description="Number of Power Line Cycles for integration")
    compliance_V: float = Field(gt=0, description="Voltage compliance limit")


class DAQ6510SwitchingModule(BaseModel):
    """Keithley 7709 switching module configuration."""

    model: Literal["Keithley_7709"] = "Keithley_7709"
    slot: int = Field(ge=1, le=3, description="Slot number in DAQ6510 (1-3)")


class DAQ6510Config(BaseModel):
    """Keithley DAQ6510 Data Acquisition Unit configuration."""

    model: Literal["Keithley_DAQ6510"] = "Keithley_DAQ6510"
    ip_address: str = Field(description="IP address for network connection")
    port: int = Field(ge=1, le=65535, description="TCP port number")
    visa_resource: str = Field(description="VISA resource string")
    command_set: Literal["SCPI"] = "SCPI"
    switching_module: DAQ6510SwitchingModule = Field(description="Multiplexer module")


class InstrumentsRootConfig(BaseModel):
    """Root schema for instruments.yaml."""

    version: int = Field(default=1, ge=1, description="Config format version")
    transport: TransportConfig = Field(description="SCPI transport settings")
    instruments: Dict[str, Any] = Field(description="Instrument definitions")

    @field_validator("instruments")
    @classmethod
    def validate_instruments(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that required instruments are present."""
        if "b2902b" not in v:
            raise ValueError("instruments.yaml must define 'b2902b' (Keysight SMU)")
        if "daq6510" not in v:
            raise ValueError("instruments.yaml must define 'daq6510' (Keithley DAQ)")
        return v


# ============================================================================
# Measurement Sequences Configuration Schema
# ============================================================================


class MeasurementDefaults(BaseModel):
    """Default measurement parameters."""

    settling_time_s: float = Field(gt=0, description="Settling time after relay change (s)")
    repeats: int = Field(ge=1, description="Number of repeats per measurement")
    contact_check_currents_A: List[float] = Field(
        description="Current levels for contact check (A)", min_length=1
    )
    characterization_currents_A: List[float] = Field(
        description="Current levels for characterization (A)", min_length=1
    )
    compliance_V: float = Field(gt=0, description="Voltage compliance limit (V)")
    contact_check_remote_sense: bool = Field(
        default=False, description="Use remote sensing for contact check"
    )
    characterization_remote_sense: bool = Field(
        default=True, description="Use remote sensing for characterization"
    )
    remote_sense: Optional[bool] = Field(
        default=None, description="Legacy global remote sensing flag"
    )

    @field_validator("contact_check_currents_A", "characterization_currents_A")
    @classmethod
    def validate_currents_positive(cls, v: List[float]) -> List[float]:
        """Ensure all currents are positive."""
        for i, current in enumerate(v):
            if current <= 0:
                raise ValueError(f"Current at index {i} must be positive, got {current}")
        return v


class PairMeasurement(BaseModel):
    """Definition of a single pair measurement."""

    id: str = Field(description="Measurement identifier")
    force_pair: List[str] = Field(
        min_length=2, max_length=2, description="Force contact pair [A/B/C/D, A/B/C/D]"
    )
    sense_pair: List[str] = Field(
        min_length=2, max_length=2, description="Sense contact pair [A/B/C/D, A/B/C/D]"
    )

    @field_validator("force_pair", "sense_pair")
    @classmethod
    def validate_contact_names(cls, v: List[str]) -> List[str]:
        """Ensure contact names are valid (A, B, C, D)."""
        for contact in v:
            if contact not in VALID_CONTACTS:
                raise ValueError(f"Contact name must be in {VALID_CONTACTS}, got '{contact}'")
        return v


class ContactCheckSequence(BaseModel):
    """Contact-check sequence with local Kelvin pair measurements."""

    description: str = Field(description="Sequence description")
    mode: str = Field(description="Measurement mode (e.g., 'kelvin_local_single_channel')")
    currents_ref: str = Field(description="Reference key to defaults['currents_..._A']")
    pair_measurements: List[PairMeasurement] = Field(
        min_length=1, description="Pair measurements to perform"
    )
    per_measurement_steps: List[str] = Field(description="Steps to execute per measurement")
    quality_metrics: Optional[List[str]] = Field(
        default=None, description="Quality metrics to compute"
    )


class TerminalMeasurement(BaseModel):
    """Four-terminal measurement definition."""

    id: str = Field(description="Measurement identifier")
    current_pair: List[str] = Field(min_length=2, max_length=2)
    voltage_pair: List[str] = Field(min_length=2, max_length=2)

    @field_validator("current_pair", "voltage_pair")
    @classmethod
    def validate_contact_names(cls, v: List[str]) -> List[str]:
        for contact in v:
            if contact not in VALID_CONTACTS:
                raise ValueError(f"Contact name must be in {VALID_CONTACTS}, got '{contact}'")
        return v


class ReciprocityPair(BaseModel):
    """Reciprocity pair, either by reference id or inline measurement definition."""

    forward: Union[str, TerminalMeasurement]
    reciprocal: Union[str, TerminalMeasurement]


class SheetResistanceSolverConfig(BaseModel):
    equation: str
    method: str
    relative_tolerance: float = Field(gt=0)
    residual_tolerance: float = Field(gt=0)
    max_iterations: int = Field(gt=0)
    approximation_only_for_diagnostics: Optional[str] = None
    diagnostics: Optional[List[str]] = None


class CharacterizationBlock(BaseModel):
    """One block inside full_characterization."""

    mode: str
    measurements: Optional[List[TerminalMeasurement]] = None
    measurement_pairs: Optional[List[ReciprocityPair]] = None
    input_measurements: Optional[List[str]] = None
    optional_input_measurements: Optional[List[str]] = None
    outputs: Optional[List[str]] = None
    sheet_resistance_solver: Optional[SheetResistanceSolverConfig] = None
    requires: Optional[List[str]] = None
    field_points_T: Optional[List[Union[float, str]]] = None

    @model_validator(mode="after")
    def validate_block_content(self) -> "CharacterizationBlock":
        if self.mode == "derived":
            if not self.input_measurements:
                raise ValueError("derived characterization blocks require input_measurements")
            return self
        if self.measurements is None and self.measurement_pairs is None:
            raise ValueError("measurement blocks require measurements or measurement_pairs")
        return self


class FullCharacterizationSequence(BaseModel):
    """Full characterization sequence composed of named measurement/derived blocks."""

    description: str
    currents_ref: str
    blocks: Dict[str, CharacterizationBlock] = Field(min_length=1)

    @field_validator("blocks")
    @classmethod
    def validate_required_blocks(
        cls, v: Dict[str, CharacterizationBlock]
    ) -> Dict[str, CharacterizationBlock]:
        required = {"van_der_pauw", "reciprocity", "anisotropy_nonuniformity"}
        if not required.issubset(v.keys()):
            raise ValueError(
                f"full_characterization blocks must include {required}, got {set(v.keys())}"
            )
        return v


class MeasurementSequencesConfig(BaseModel):
    """Named sequence definitions used by the planner."""

    contact_check: ContactCheckSequence
    full_characterization: FullCharacterizationSequence


class MeasurementSequencesAssumptions(BaseModel):
    """Assumptions about sample and measurement setup."""

    sample_contacts: List[str] = Field(
        min_length=4, max_length=4, description="Sample contacts [A, B, C, D]"
    )
    contact_order: str = Field(description="Contact arrangement order")
    current_reversal: Literal["smu", "relays"] = Field(description="How current is reversed")
    relay_state_between_polarities: str = Field(
        description="Relay behavior between polarity changes"
    )
    multiplexer: str = Field(description="Multiplexer model")
    meter_source: str = Field(description="Voltage measurement source")

    @field_validator("sample_contacts")
    @classmethod
    def validate_sample_contacts(cls, v: List[str]) -> List[str]:
        """Ensure sample contacts are A, B, C, D."""
        expected = {"A", "B", "C", "D"}
        if set(v) != expected:
            raise ValueError(f"Sample contacts must be {sorted(expected)}, got {v}")
        return v


class MeasurementSequencesRootConfig(BaseModel):
    """Root schema for measurement_sequences.yaml."""

    version: int = Field(default=1, ge=1, description="Config format version")
    assumptions: MeasurementSequencesAssumptions = Field(description="Sample assumptions")
    defaults: MeasurementDefaults = Field(description="Default parameters")
    record_fields: List[str] = Field(min_length=1, description="Fields in measurement records")
    sequences: MeasurementSequencesConfig = Field(description="Named measurement sequences")


# ============================================================================
# Routing Template Configuration Schema
# ============================================================================


class MatrixConfig(BaseModel):
    """Multiplexer matrix configuration."""

    model: Literal["Keithley_7709"] = "Keithley_7709"
    daq_slot: int = Field(ge=1, le=3, description="DAQ slot containing matrix")
    rows: int = Field(gt=0, description="Number of rows in matrix")
    columns: int = Field(gt=0, description="Number of columns in matrix")
    crosspoint_channel_formula: str = Field(description="Formula for crosspoint channel numbering")
    daq_channel_formula: str = Field(description="Formula for DAQ channel numbering")
    used_pole: str = Field(description="Used pole (HI or LO)")
    unused_pole: str = Field(description="Unused pole state")


class KelvinSingleChannelRows(BaseModel):
    """Row definitions for single-channel Kelvin measurement."""

    smu_channel: int = Field(ge=1, description="SMU channel")
    force_hi: int = Field(ge=1, description="Force HIGH row")
    force_lo: int = Field(ge=1, description="Force LOW row")
    sense_hi: int = Field(ge=1, description="Sense HIGH row")
    sense_lo: int = Field(ge=1, description="Sense LOW row")


class DualChannelRowsOptional(BaseModel):
    """Row definitions for dual-channel measurement (optional)."""

    current_smu_channel: int = Field(ge=1, description="Current SMU channel")
    voltage_smu_channel: int = Field(ge=1, description="Voltage SMU channel")
    ch1_force_hi: int = Field(ge=1, description="Channel 1 force HIGH row")
    ch1_force_lo: int = Field(ge=1, description="Channel 1 force LOW row")
    ch1_sense_hi: int = Field(ge=1, description="Channel 1 sense HIGH row")
    ch1_sense_lo: int = Field(ge=1, description="Channel 1 sense LOW row")
    ch2_hi: int = Field(ge=1, description="Channel 2 HIGH row")
    ch2_lo: int = Field(ge=1, description="Channel 2 LOW row")


class RoutingTemplateRootConfig(BaseModel):
    """Root schema for routing_template.yaml."""

    version: int = Field(default=1, ge=1, description="Config format version")
    description: str = Field(default="", description="Template description")
    matrix: MatrixConfig = Field(description="Matrix configuration")
    sample_columns: Dict[str, int] = Field(
        description="Contact-to-column mapping {A: col, B: col, ...}"
    )
    single_channel_kelvin_rows: KelvinSingleChannelRows = Field(
        description="Single-channel Kelvin rows"
    )
    dual_channel_rows_optional: Optional[DualChannelRowsOptional] = Field(
        default=None, description="Optional dual-channel row definitions"
    )
    routing_rules: Dict[str, Any] = Field(description="Routing rules per measurement mode")

    @field_validator("sample_columns")
    @classmethod
    def validate_sample_columns(cls, v: Dict[str, int]) -> Dict[str, int]:
        """Ensure sample columns have A, B, C, D and valid column indices."""
        required = {"A", "B", "C", "D"}
        if set(v.keys()) != required:
            raise ValueError(f"sample_columns must have contacts {required}, got {set(v.keys())}")

        for contact, col in v.items():
            if col < 1:
                raise ValueError(f"Column for {contact} must be >= 1, got {col}")

        return v

    @model_validator(mode="after")
    def validate_matrix_bounds_and_uniqueness(self) -> "RoutingTemplateRootConfig":
        for contact, col in self.sample_columns.items():
            if col > self.matrix.columns:
                raise ValueError(
                    f"Column for contact {contact} must be <= matrix columns "
                    f"({self.matrix.columns}), got {col}"
                )

        rows = self.single_channel_kelvin_rows
        row_values = {
            "force_hi": rows.force_hi,
            "force_lo": rows.force_lo,
            "sense_hi": rows.sense_hi,
            "sense_lo": rows.sense_lo,
        }
        for name, row in row_values.items():
            if row > self.matrix.rows:
                raise ValueError(
                    f"Row {name} must be <= matrix rows ({self.matrix.rows}), got {row}"
                )
        if len(set(row_values.values())) != len(row_values):
            raise ValueError(f"single_channel_kelvin_rows must be unique, got {row_values}")

        if self.dual_channel_rows_optional is not None:
            dual = self.dual_channel_rows_optional
            dual_values = {
                "ch1_force_hi": dual.ch1_force_hi,
                "ch1_force_lo": dual.ch1_force_lo,
                "ch1_sense_hi": dual.ch1_sense_hi,
                "ch1_sense_lo": dual.ch1_sense_lo,
                "ch2_hi": dual.ch2_hi,
                "ch2_lo": dual.ch2_lo,
            }
            for name, row in dual_values.items():
                if row > self.matrix.rows:
                    raise ValueError(
                        f"Row {name} must be <= matrix rows ({self.matrix.rows}), got {row}"
                    )
        return self
