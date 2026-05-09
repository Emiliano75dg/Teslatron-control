from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
import json

DEFAULT_INSERT_PROFILE_ID = "fisher_probe"
DEFAULT_INSERT_PROFILE_NAME = "Fisher probe"
DEFAULT_INSERT_PROFILE_DESCRIPTION = "Default cryostat configuration for the Fisher probe insert."


@dataclass(slots=True)
class MercuryITCConfig:
    address: str = "ASRL7::INSTR"
    timeout_ms: int = 3000
    read_termination: str = "\n"
    write_termination: str = "\n"
    probe_signal: str = "DB8.T1"
    probe_loop: str = "DB8.T1"
    vti_signal: str = "MB1.T1"
    vti_loop: str = "MB1.T1"
    pressure: str = "DB5.P1"


@dataclass(slots=True)
class MercuryIPSConfig:
    address: str = "ASRL8::INSTR"
    timeout_ms: int = 3000
    read_termination: str = "\n"
    write_termination: str = "\n"
    magnet_group: str = "GRPZ"
    magnet_temperature: str = "MB1.T1"
    pt1_temperature: str = "DB8.T1"
    pt2_temperature: str = "DB7.T1"
    switch_on_delay_s: float = 300.0
    switch_off_delay_s: float = 300.0
    command_delay_s: float = 0.1


@dataclass(slots=True)
class SafetyConfig:
    min_temperature_K: float = 0.0
    max_temperature_K: float = 350.0
    max_temperature_rate_K_per_min: float = 5.0
    max_field_T: float = 12.0
    max_field_rate_T_per_min: float = 0.5


@dataclass(slots=True)
class InsertCapabilitiesConfig:
    temperature_control: bool = True
    sample_loop: bool = True
    vti_loop: bool = True
    gas_control: bool = True
    field_control: bool = True
    pid_control: bool = True
    fixed_heater: bool = True


@dataclass(slots=True)
class MercurySensorSetupConfig:
    sensor_type: str = ""
    excitation_type: str = ""
    excitation_magnitude: str = ""
    calibration: str = ""


@dataclass(slots=True)
class InsertProfileConfig:
    name: str = DEFAULT_INSERT_PROFILE_NAME
    description: str = DEFAULT_INSERT_PROFILE_DESCRIPTION
    sample_thermometer: str = ""
    notes: str = ""
    capabilities: InsertCapabilitiesConfig = field(default_factory=InsertCapabilitiesConfig)
    sample_sensor_options: list[str] = field(default_factory=list)
    default_sample_sensor: str | None = None
    itc: MercuryITCConfig = field(default_factory=MercuryITCConfig)


@dataclass(slots=True)
class CryostatServiceConfig:
    backend: str = "mock"
    read_only: bool = False
    poll_interval_s: float = 1.0
    log_interval_s: float = 20.0
    log_dir: str = "data"
    log_actions: bool = True
    sample_thermometer: str = ""
    active_insert: str | None = None
    insert_profiles: dict[str, InsertProfileConfig] = field(default_factory=dict)
    sample_sensor_presets: dict[str, MercurySensorSetupConfig] = field(default_factory=dict)
    active_sample_sensor: str | None = None
    itc: MercuryITCConfig = field(default_factory=MercuryITCConfig)
    ips: MercuryIPSConfig = field(default_factory=MercuryIPSConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def apply_insert_profile(self, profile_id: str) -> None:
        try:
            profile = self.insert_profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"Unknown insert profile: {profile_id}") from exc
        self.active_insert = profile_id
        self.sample_thermometer = profile.sample_thermometer
        self.itc = replace(profile.itc)
        self.active_sample_sensor = _selected_sensor_for_profile(
            profile,
            self.sample_sensor_presets,
            self.active_sample_sensor,
        )

    def active_insert_profile(self) -> InsertProfileConfig | None:
        if self.active_insert is None:
            return None
        return self.insert_profiles.get(self.active_insert)

    def active_capabilities(self) -> InsertCapabilitiesConfig:
        profile = self.active_insert_profile()
        if profile is None:
            return InsertCapabilitiesConfig()
        return _normalized_insert_capabilities(profile.capabilities)

    def available_sample_sensor_presets(self) -> dict[str, MercurySensorSetupConfig]:
        profile = self.active_insert_profile()
        if profile is None or not profile.sample_sensor_options:
            return {}
        return {
            preset_id: self.sample_sensor_presets[preset_id]
            for preset_id in profile.sample_sensor_options
            if preset_id in self.sample_sensor_presets
        }

    def active_sample_sensor_setup(self) -> MercurySensorSetupConfig | None:
        if self.active_sample_sensor is None:
            return None
        return self.sample_sensor_presets.get(self.active_sample_sensor)


def load_config(path: str | Path | None = None) -> CryostatServiceConfig:
    if path is None:
        return config_from_mapping({})

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    raw = config_path.read_text()
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "YAML config requires PyYAML. Use JSON or install pyyaml."
            ) from exc
        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw)

    return config_from_mapping(data)


def config_from_mapping(data: dict[str, Any]) -> CryostatServiceConfig:
    cryostat = data.get("cryostat", data)
    itc = MercuryITCConfig(**cryostat.get("itc", {}))
    ips = MercuryIPSConfig(**cryostat.get("ips", {}))
    safety = SafetyConfig(**cryostat.get("safety", {}))
    raw_profiles = cryostat.get("insert_profiles", cryostat.get("inserts", {})) or {}
    raw_sensor_presets = cryostat.get("sample_sensor_presets", cryostat.get("sensor_presets", {})) or {}
    sample_sensor_presets = {
        preset_id: MercurySensorSetupConfig(**(preset_data or {}))
        for preset_id, preset_data in raw_sensor_presets.items()
    }
    _merge_legacy_profile_sensors(raw_profiles, sample_sensor_presets)
    insert_profiles = {
        profile_id: _insert_profile_from_mapping(
            profile_id,
            profile_data,
            itc,
            ips,
            sample_sensor_presets,
        )
        for profile_id, profile_data in raw_profiles.items()
    }
    active_insert = cryostat.get("active_insert")
    if not insert_profiles:
        fallback_profile = _insert_profile_from_mapping(
            DEFAULT_INSERT_PROFILE_ID,
            {
                "name": DEFAULT_INSERT_PROFILE_NAME,
                "description": DEFAULT_INSERT_PROFILE_DESCRIPTION,
                "sample_thermometer": cryostat.get("sample_thermometer", ""),
                "sample_sensor_options": [],
            },
            itc,
            ips,
            sample_sensor_presets,
        )
        insert_profiles = {DEFAULT_INSERT_PROFILE_ID: fallback_profile}
        active_insert = active_insert or DEFAULT_INSERT_PROFILE_ID
    config = CryostatServiceConfig(
        backend=cryostat.get("backend", "mock"),
        read_only=bool(cryostat.get("read_only", False)),
        poll_interval_s=float(cryostat.get("poll_interval_s", 1.0)),
        log_interval_s=float(cryostat.get("log_interval_s", 20.0)),
        log_dir=cryostat.get("log_dir", "data"),
        log_actions=bool(cryostat.get("log_actions", True)),
        sample_thermometer=cryostat.get("sample_thermometer", ""),
        active_insert=active_insert,
        insert_profiles=insert_profiles,
        sample_sensor_presets=sample_sensor_presets,
        active_sample_sensor=cryostat.get("active_sample_sensor"),
        itc=itc,
        ips=ips,
        safety=safety,
    )
    if config.insert_profiles:
        selected_insert = config.active_insert or next(iter(config.insert_profiles))
        config.apply_insert_profile(selected_insert)
    return config


def _insert_profile_from_mapping(
    profile_id: str,
    data: dict[str, Any],
    default_itc: MercuryITCConfig,
    default_ips: MercuryIPSConfig,
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
) -> InsertProfileConfig:
    if data.get("ips"):
        raise ValueError(
            f"Insert profile {profile_id!r} cannot override IPS settings; "
            "IPS configuration is global"
        )
    itc_data = {
        **asdict(default_itc),
        **(data.get("itc", {}) or {}),
    }
    sample_sensor_options = _sensor_option_ids(profile_id, data, sample_sensor_presets)
    default_sample_sensor = _default_sensor_option_id(profile_id, data, sample_sensor_presets)
    _validate_sensor_options(
        profile_id,
        sample_sensor_options,
        default_sample_sensor,
        sample_sensor_presets,
    )
    return InsertProfileConfig(
        name=data.get("name", DEFAULT_INSERT_PROFILE_NAME if profile_id == DEFAULT_INSERT_PROFILE_ID else profile_id),
        description=data.get(
            "description",
            DEFAULT_INSERT_PROFILE_DESCRIPTION if profile_id == DEFAULT_INSERT_PROFILE_ID else "",
        ),
        sample_thermometer=data.get("sample_thermometer", ""),
        notes=data.get("notes", ""),
        capabilities=_normalized_insert_capabilities(
            InsertCapabilitiesConfig(**(data.get("capabilities", {}) or {}))
        ),
        sample_sensor_options=sample_sensor_options,
        default_sample_sensor=default_sample_sensor,
        itc=MercuryITCConfig(**itc_data),
    )


def _normalized_insert_capabilities(
    capabilities: InsertCapabilitiesConfig,
) -> InsertCapabilitiesConfig:
    return InsertCapabilitiesConfig(
        temperature_control=capabilities.temperature_control,
        sample_loop=capabilities.sample_loop,
        vti_loop=capabilities.vti_loop,
        gas_control=capabilities.gas_control,
        field_control=capabilities.field_control,
        pid_control=capabilities.pid_control,
        fixed_heater=capabilities.fixed_heater,
    )


def _sensor_option_ids(
    profile_id: str,
    data: dict[str, Any],
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
) -> list[str]:
    raw_options = data.get("sample_sensor_options")
    if raw_options is None and data.get("sample_sensor"):
        legacy_sensor = MercurySensorSetupConfig(**(data.get("sample_sensor", {}) or {}))
        if any(asdict(legacy_sensor).values()):
            legacy_id = _legacy_sensor_preset_id(data, profile_id)
            return [legacy_id]
    if raw_options is None:
        return []
    return [str(option_id) for option_id in raw_options]


def _default_sensor_option_id(
    profile_id: str,
    data: dict[str, Any],
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
) -> str | None:
    explicit = data.get("default_sample_sensor")
    if explicit:
        return str(explicit)
    if data.get("sample_sensor"):
        return _legacy_sensor_preset_id(data, profile_id)
    options = _sensor_option_ids(profile_id, data, sample_sensor_presets)
    return options[0] if options else None


def _selected_sensor_for_profile(
    profile: InsertProfileConfig,
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
    current_sensor_id: str | None,
) -> str | None:
    available = [
        sensor_id
        for sensor_id in profile.sample_sensor_options
        if sensor_id in sample_sensor_presets
    ]
    if current_sensor_id in available:
        return current_sensor_id
    if profile.default_sample_sensor in available:
        return profile.default_sample_sensor
    return available[0] if available else None


def _validate_sensor_options(
    profile_id: str,
    sample_sensor_options: list[str],
    default_sample_sensor: str | None,
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
) -> None:
    missing = [
        sensor_id for sensor_id in sample_sensor_options if sensor_id not in sample_sensor_presets
    ]
    if missing:
        raise ValueError(
            f"Insert profile {profile_id!r} references unknown sample sensor presets: "
            + ", ".join(sorted(missing))
        )
    if default_sample_sensor is not None and default_sample_sensor not in sample_sensor_options:
        raise ValueError(
            f"Insert profile {profile_id!r} has default_sample_sensor {default_sample_sensor!r} "
            "which is not listed in sample_sensor_options"
        )


def _merge_legacy_profile_sensors(
    raw_profiles: dict[str, Any],
    sample_sensor_presets: dict[str, MercurySensorSetupConfig],
) -> None:
    for profile_id, profile_data in raw_profiles.items():
        raw_sensor = (profile_data or {}).get("sample_sensor")
        if not raw_sensor:
            continue
        sensor = MercurySensorSetupConfig(**raw_sensor)
        if not any(asdict(sensor).values()):
            continue
        preset_id = _legacy_sensor_preset_id(profile_data or {}, profile_id)
        sample_sensor_presets.setdefault(preset_id, sensor)


def _legacy_sensor_preset_id(data: dict[str, Any], profile_id: str = "legacy") -> str:
    return str(data.get("default_sample_sensor") or f"{profile_id}_sensor")
