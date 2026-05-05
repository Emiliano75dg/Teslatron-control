from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import asdict
from pathlib import Path
from typing import Any
import json


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


@dataclass(slots=True)
class SafetyConfig:
    min_temperature_K: float = 0.0
    max_temperature_K: float = 350.0
    max_temperature_rate_K_per_min: float = 5.0
    max_field_T: float = 12.0
    max_field_rate_T_per_min: float = 0.5


@dataclass(slots=True)
class CryostatServiceConfig:
    backend: str = "mock"
    read_only: bool = False
    poll_interval_s: float = 1.0
    log_interval_s: float = 20.0
    log_path: str = "data/cryostat_environment.csv"
    itc: MercuryITCConfig = field(default_factory=MercuryITCConfig)
    ips: MercuryIPSConfig = field(default_factory=MercuryIPSConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: str | Path | None = None) -> CryostatServiceConfig:
    if path is None:
        return CryostatServiceConfig()

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
    return CryostatServiceConfig(
        backend=cryostat.get("backend", "mock"),
        read_only=bool(cryostat.get("read_only", False)),
        poll_interval_s=float(cryostat.get("poll_interval_s", 1.0)),
        log_interval_s=float(cryostat.get("log_interval_s", 20.0)),
        log_path=cryostat.get("log_path", "data/cryostat_environment.csv"),
        itc=itc,
        ips=ips,
        safety=safety,
    )
