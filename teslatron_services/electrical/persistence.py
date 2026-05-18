from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RunArtifactPaths:
    run_id: str
    run_slug: str
    run_dir: Path
    jsonl_path: Path
    csv_path: Path
    metadata_path: Path
    config_snapshot_path: Path
    cryostat_snapshot_start_path: Path
    cryostat_snapshot_end_path: Path

    def output_paths(self) -> dict[str, str]:
        return {
            "jsonl": str(self.jsonl_path),
            "electrical_csv": str(self.csv_path),
            "metadata": str(self.metadata_path),
            "config_snapshot": str(self.config_snapshot_path),
            "cryostat_snapshot_start": str(self.cryostat_snapshot_start_path),
            "cryostat_snapshot_end": str(self.cryostat_snapshot_end_path),
        }


class RunArtifactStore:
    def __init__(self, save_dir: str):
        self.save_dir = Path(save_dir)
        self._active_runs: dict[str, RunArtifactPaths] = {}

    def begin_run(self, run_id: str) -> RunArtifactPaths:
        active = self._active_runs.get(run_id)
        if active is not None:
            return active

        base_slug = _slug(run_id)
        today = datetime.now().strftime("%Y-%m-%d")
        date_dir = self.save_dir / today
        run_dir = _next_available_directory(date_dir / base_slug)
        run_slug = run_dir.name
        paths = RunArtifactPaths(
            run_id=run_id,
            run_slug=run_slug,
            run_dir=run_dir,
            jsonl_path=run_dir / f"{run_slug}.jsonl",
            csv_path=run_dir / f"{run_slug}_electrical.csv",
            metadata_path=run_dir / "metadata.json",
            config_snapshot_path=run_dir / "config_snapshot.json",
            cryostat_snapshot_start_path=run_dir / "cryostat_snapshot_start.json",
            cryostat_snapshot_end_path=run_dir / "cryostat_snapshot_end.json",
        )
        self._active_runs[run_id] = paths
        return paths

    def paths_for(self, run_id: str) -> RunArtifactPaths:
        active = self._active_runs.get(run_id)
        if active is not None:
            return active

        base_slug = _slug(run_id)
        today = datetime.now().strftime("%Y-%m-%d")
        run_dir = self.save_dir / today / base_slug
        return RunArtifactPaths(
            run_id=run_id,
            run_slug=base_slug,
            run_dir=run_dir,
            jsonl_path=run_dir / f"{base_slug}.jsonl",
            csv_path=run_dir / f"{base_slug}_electrical.csv",
            metadata_path=run_dir / "metadata.json",
            config_snapshot_path=run_dir / "config_snapshot.json",
            cryostat_snapshot_start_path=run_dir / "cryostat_snapshot_start.json",
            cryostat_snapshot_end_path=run_dir / "cryostat_snapshot_end.json",
        )

    def initialize_run(
        self,
        run_id: str,
        *,
        metadata: dict[str, Any],
        config_snapshot: dict[str, Any],
        cryostat_snapshot_start: dict[str, Any],
    ) -> RunArtifactPaths:
        paths = self.begin_run(run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(paths.metadata_path, metadata)
        _write_json(paths.config_snapshot_path, config_snapshot)
        _write_json(paths.cryostat_snapshot_start_path, cryostat_snapshot_start)
        return paths

    def update_metadata(self, run_id: str, metadata: dict[str, Any]) -> Path:
        paths = self.begin_run(run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(paths.metadata_path, metadata)
        return paths.metadata_path

    def write_cryostat_snapshot_end(self, run_id: str, snapshot: dict[str, Any]) -> Path:
        paths = self.begin_run(run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(paths.cryostat_snapshot_end_path, snapshot)
        return paths.cryostat_snapshot_end_path


class JsonlMeasurementWriter:
    def __init__(self, save_dir: str, *, artifacts: RunArtifactStore | None = None):
        self._artifacts = artifacts or RunArtifactStore(save_dir)

    def append_event(self, run_id: str, event: dict[str, Any]) -> Path:
        path = self.run_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        return path

    def run_path(self, run_id: str) -> Path:
        return self._artifacts.paths_for(run_id).jsonl_path


class ElectricalCsvMeasurementWriter:
    _BASE_COLUMNS = [
        "run_id",
        "plan_id",
        "instrument",
        "timestamp_unix_s",
        "timestamp_iso",
        "time_relative_s",
        "sample_temperature_K",
        "field_T",
        "safe_to_measure",
        "vti_temperature_K",
        "pressure_mbar",
        "cryostat_timestamp",
    ]

    def __init__(self, save_dir: str, *, artifacts: RunArtifactStore | None = None):
        self._artifacts = artifacts or RunArtifactStore(save_dir)
        self._active_runs: dict[str, _CsvRunState] = {}

    def begin_run(self, run_id: str) -> Path:
        csv_path = self._artifacts.begin_run(run_id).csv_path
        self._active_runs[run_id] = _CsvRunState(path=csv_path, columns=list(self._BASE_COLUMNS))
        return csv_path

    def csv_path(self, run_id: str) -> Path:
        state = self._active_runs.get(run_id)
        if state is not None:
            return state.path
        return self._artifacts.paths_for(run_id).csv_path

    def append_row(self, run_id: str, row: dict[str, Any]) -> Path:
        state = self._active_runs.get(run_id)
        if state is None:
            self.begin_run(run_id)
            state = self._active_runs[run_id]

        new_columns = [key for key in row if key not in state.columns]
        if new_columns:
            state.columns.extend(new_columns)
            self._rewrite_with_header(state.path, state.columns, row)
        else:
            self._append_only(state.path, state.columns, row)
        return state.path

    def _append_only(self, path: Path, columns: list[str], row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(_normalize_csv_row(columns, row))

    def _rewrite_with_header(self, path: Path, columns: list[str], row: dict[str, Any]) -> None:
        existing_rows: list[dict[str, str]] = []
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                existing_rows.extend(dict(item) for item in reader)

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for existing in existing_rows:
                writer.writerow(_normalize_csv_row(columns, existing))
            writer.writerow(_normalize_csv_row(columns, row))


class _CsvRunState:
    def __init__(self, *, path: Path, columns: list[str]):
        self.path = path
        self.columns = columns


def flatten_measurement(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": _jsonify_if_needed(payload)}
    flattened: dict[str, Any] = {}
    _flatten_into(flattened, payload)
    return flattened


def _flatten_into(flattened: dict[str, Any], payload: dict[str, Any], prefix: str = "") -> None:
    for raw_key, value in payload.items():
        key = _column_fragment(raw_key)
        full_key = f"{prefix}_{key}" if prefix else key
        if isinstance(value, dict):
            _flatten_into(flattened, value, full_key)
            continue
        flattened[full_key] = _jsonify_if_needed(value)


def _column_fragment(value: Any) -> str:
    text = "_".join(str(value).strip().split())
    return text or "value"


def _jsonify_if_needed(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _normalize_csv_row(columns: list[str], row: dict[str, Any]) -> dict[str, Any]:
    normalized = {column: "" for column in columns}
    for key, value in row.items():
        if key not in normalized:
            continue
        normalized[key] = "" if value is None else _jsonify_if_needed(value)
    return normalized


def _next_available_directory(path: Path) -> Path:
    if not path.exists():
        return path
    parent = path.parent
    stem = path.name
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _slug(value: str) -> str:
    chars = []
    for char in value.strip():
        if char.isalnum():
            chars.append(char.lower())
        elif char in {"-", "_"}:
            chars.append(char)
        elif char.isspace():
            chars.append("_")
    slug = "".join(chars).strip("_")
    return slug[:80] or "run"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
