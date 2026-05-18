from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class CryostatCacheState:
    connected: bool = False
    last_fetch_at: float | None = None
    last_error: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MeasurementRunState:
    status: str = "idle"
    run_id: str | None = None
    instrument: str | None = None
    plan_id: str | None = None
    trigger_signal: str | None = None
    interval_s: float | None = None
    max_points: int | None = None
    points_acquired: int = 0
    started_at: float | None = None
    run_start_monotonic_s: float | None = None
    stopped_at: float | None = None
    last_event: dict[str, Any] | None = None
    output_path: str | None = None
    electrical_csv_path: str | None = None
    output_paths: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class ElectricalServiceState:
    timestamp: float = field(default_factory=time)
    cryostat: CryostatCacheState = field(default_factory=CryostatCacheState)
    run: MeasurementRunState = field(default_factory=MeasurementRunState)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
