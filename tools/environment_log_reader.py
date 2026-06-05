from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Sequence, TextIO

import pandas as pd

TEMPERATURE_SERIES: dict[str, dict[str, str]] = {
    "sample_temperature_K": {"label": "Sample (K)", "color": "#c0392b"},
    "vti_temperature_K": {"label": "VTI (K)", "color": "#1d4e89"},
    "magnet_temperature_K": {"label": "Magnet (K)", "color": "#2a9d8f"},
    "pt1_temperature_K": {"label": "PT1 (K)", "color": "#3a86ff"},
    "pt2_temperature_K": {"label": "PT2 (K)", "color": "#f4a261"},
}

MAGNETICS_SERIES_LEFT: dict[str, dict[str, str]] = {
    "B_T": {"label": "Field (T)", "color": "#2b9348"},
    "field_output_current_A": {"label": "Current (A)", "color": "#386fa4"},
    "field_output_voltage_V": {"label": "Voltage (V)", "color": "#d0006f"},
}

MAGNETICS_SERIES_RIGHT: dict[str, dict[str, str]] = {
    "pressure_mbar": {"label": "Pressure (mbar)", "color": "#bc6c25"},
    "needle_valve_percent": {"label": "Needle (%)", "color": "#7b2cbf"},
}

LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo
SourceType = str | Path | TextIO | BinaryIO | Any


def format_float(value: Any, decimals: int = 2) -> str | None:
    """Return a compact numeric string or None for missing values."""
    if pd.isna(value):
        return None
    return f"{float(value):.{decimals}f}"


def values_differ(previous: Any, current: Any, threshold: float = 0.0) -> bool:
    """Compare possibly-missing numeric values with a tolerance."""
    if pd.isna(previous) and pd.isna(current):
        return False
    if pd.isna(previous) or pd.isna(current):
        return True
    return abs(float(current) - float(previous)) > threshold


def normalize_time_data(series: pd.Series) -> pd.Series:
    """
    Normalize timestamps to the local machine timezone for display.
    Logs are written in UTC, so we convert explicitly.
    """
    if pd.api.types.is_numeric_dtype(series):
        parsed = pd.to_datetime(series, unit="s", utc=True)
    else:
        parsed = pd.to_datetime(series)
        if getattr(parsed.dt, "tz", None) is None:
            parsed = parsed.dt.tz_localize("UTC")
        else:
            parsed = parsed.dt.tz_convert("UTC")
    return parsed.dt.tz_convert(LOCAL_TIMEZONE)


def _source_name(source: SourceType) -> str:
    """Derive a display name for a filesystem path or uploaded file object."""
    name = getattr(source, "name", None)
    if name:
        return Path(name).name
    return Path(str(source)).name


def load_and_prepare_logs(file_paths: Sequence[SourceType]) -> tuple[pd.DataFrame, str | None, int]:
    """Read, filter, merge, and normalize one or more cryostat CSV logs."""
    if not file_paths:
        raise ValueError("No files were provided.")

    frames: list[pd.DataFrame] = []
    filtered_rows_total = 0

    for source in file_paths:
        if hasattr(source, "seek"):
            source.seek(0)
        df = pd.read_csv(source)
        if "backend" in df.columns:
            original_rows = len(df)
            df = df[df["backend"] != "mock"].copy()
            filtered_rows_total += original_rows - len(df)
        df["__source_file__"] = _source_name(source)
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if merged.empty:
        return merged, None, filtered_rows_total

    time_col = None
    if "timestamp_iso" in merged.columns:
        time_col = "timestamp_iso"
    elif "timestamp" in merged.columns:
        time_col = "timestamp"

    if not time_col:
        raise ValueError("No 'timestamp' or 'timestamp_iso' column found in the CSV.")

    merged[time_col] = normalize_time_data(merged[time_col])
    merged = merged.sort_values(by=[time_col, "__source_file__"], kind="stable").reset_index(drop=True)
    return merged, time_col, filtered_rows_total


def build_event_list(df: pd.DataFrame, time_col: str) -> list[tuple[pd.Timestamp, str]]:
    """Reconstruct notable events from state transitions between log rows."""
    if df.empty:
        return []

    events: list[tuple[pd.Timestamp, str]] = []
    previous = None

    for _, row in df.iterrows():
        timestamp = row[time_col]
        if previous is None:
            events.append((timestamp, f"Acquisition start: {row.get('mode', 'unknown mode')}"))
            previous = row
            continue

        if row.get("mode") != previous.get("mode") and pd.notna(row.get("mode")):
            events.append((timestamp, f"Mode -> {row['mode']}"))

        if row.get("safety_level") != previous.get("safety_level") and pd.notna(row.get("safety_level")):
            message = row.get("safety_message")
            suffix = f" ({message})" if isinstance(message, str) and message.strip() else ""
            events.append((timestamp, f"Safety -> {row['safety_level']}{suffix}"))

        if row.get("field_action") != previous.get("field_action") and pd.notna(row.get("field_action")):
            target = format_float(row.get("field_target_T"), 3)
            suffix = f" toward {target} T" if target is not None else ""
            events.append((timestamp, f"Field action -> {row['field_action']}{suffix}"))

        if row.get("field_ramping") != previous.get("field_ramping"):
            if bool(row.get("field_ramping")):
                target = format_float(row.get("field_target_T"), 3)
                rate = format_float(row.get("field_rate_T_per_min"), 3)
                details = []
                if target is not None:
                    details.append(f"target {target} T")
                if rate is not None:
                    details.append(f"rate {rate} T/min")
                suffix = f" ({', '.join(details)})" if details else ""
                events.append((timestamp, f"Field ramp started{suffix}"))
            else:
                field_now = format_float(row.get("B_T"), 3)
                suffix = f" at {field_now} T" if field_now is not None else ""
                events.append((timestamp, f"Field ramp stopped{suffix}"))

        if row.get("sample_ramping") != previous.get("sample_ramping"):
            if bool(row.get("sample_ramping")):
                target = format_float(row.get("sample_target_K"), 2)
                rate = format_float(row.get("sample_rate_K_per_min"), 2)
                details = []
                if target is not None:
                    details.append(f"target {target} K")
                if rate is not None:
                    details.append(f"rate {rate} K/min")
                suffix = f" ({', '.join(details)})" if details else ""
                events.append((timestamp, f"Sample ramp started{suffix}"))
            else:
                temp_now = format_float(row.get("sample_temperature_K"), 2)
                suffix = f" at {temp_now} K" if temp_now is not None else ""
                events.append((timestamp, f"Sample ramp stopped{suffix}"))

        if row.get("vti_ramping") != previous.get("vti_ramping"):
            if bool(row.get("vti_ramping")):
                target = format_float(row.get("vti_target_K"), 2)
                rate = format_float(row.get("vti_rate_K_per_min"), 2)
                details = []
                if target is not None:
                    details.append(f"target {target} K")
                if rate is not None:
                    details.append(f"rate {rate} K/min")
                suffix = f" ({', '.join(details)})" if details else ""
                events.append((timestamp, f"VTI ramp started{suffix}"))
            else:
                temp_now = format_float(row.get("vti_temperature_K"), 2)
                suffix = f" at {temp_now} K" if temp_now is not None else ""
                events.append((timestamp, f"VTI ramp stopped{suffix}"))

        if row.get("sample_stable") != previous.get("sample_stable") and bool(row.get("sample_stable")):
            temp_now = format_float(row.get("sample_temperature_K"), 2)
            suffix = f" near {temp_now} K" if temp_now is not None else ""
            events.append((timestamp, f"Sample stable{suffix}"))

        if row.get("vti_stable") != previous.get("vti_stable") and bool(row.get("vti_stable")):
            temp_now = format_float(row.get("vti_temperature_K"), 2)
            suffix = f" near {temp_now} K" if temp_now is not None else ""
            events.append((timestamp, f"VTI stable{suffix}"))

        if values_differ(previous.get("sample_target_K"), row.get("sample_target_K"), threshold=0.15):
            target = format_float(row.get("sample_target_K"), 2)
            if target is not None:
                events.append((timestamp, f"Sample target -> {target} K"))

        if values_differ(previous.get("vti_target_K"), row.get("vti_target_K"), threshold=0.15):
            target = format_float(row.get("vti_target_K"), 2)
            if target is not None:
                events.append((timestamp, f"VTI target -> {target} K"))

        if values_differ(previous.get("field_target_T"), row.get("field_target_T"), threshold=0.01):
            target = format_float(row.get("field_target_T"), 3)
            if target is not None:
                events.append((timestamp, f"Field target -> {target} T"))

        if values_differ(previous.get("pressure_target_mbar"), row.get("pressure_target_mbar"), threshold=0.05):
            target = format_float(row.get("pressure_target_mbar"), 2)
            if target is not None:
                events.append((timestamp, f"Pressure target -> {target} mbar"))

        if values_differ(previous.get("needle_valve_percent"), row.get("needle_valve_percent"), threshold=4.0):
            needle = format_float(row.get("needle_valve_percent"), 1)
            if needle is not None:
                events.append((timestamp, f"Needle valve -> {needle}%"))

        if row.get("pressure_mode") != previous.get("pressure_mode") and pd.notna(row.get("pressure_mode")):
            events.append((timestamp, f"Pressure mode -> {row['pressure_mode']}"))

        if row.get("switch_heater_status") != previous.get("switch_heater_status") and pd.notna(row.get("switch_heater_status")):
            events.append((timestamp, f"Switch heater -> {row['switch_heater_status']}"))

        if row.get("switch_heater_target_status") != previous.get("switch_heater_target_status") and pd.notna(row.get("switch_heater_target_status")):
            events.append((timestamp, f"Switch heater target -> {row['switch_heater_target_status']}"))

        previous = row

    end_mode = df.iloc[-1].get("mode", "unknown mode")
    events.append((df.iloc[-1][time_col], f"Latest state: {end_mode}"))

    deduped_events: list[tuple[pd.Timestamp, str]] = []
    last_key = None
    for timestamp, description in events:
        key = (timestamp, description)
        if key != last_key:
            deduped_events.append((timestamp, description))
        last_key = key
    return deduped_events


def summarize_log(df: pd.DataFrame, time_col: str | None, file_paths: Sequence[SourceType]) -> dict[str, Any]:
    """Return a compact summary for header metrics and app state."""
    file_names = [_source_name(path) for path in file_paths]
    summary: dict[str, Any] = {
        "samples": len(df),
        "duration_minutes": 0,
        "start": None,
        "end": None,
        "files": len(file_names),
        "file_names": file_names,
        "timezone": getattr(LOCAL_TIMEZONE, "tzname", lambda _dt: str(LOCAL_TIMEZONE))(None),
        "latest_mode": None,
        "backend": None,
        "safety_level": None,
    }

    if df.empty or not time_col or time_col not in df.columns:
        return summary

    start_ts = df[time_col].min()
    end_ts = df[time_col].max()
    duration = end_ts - start_ts
    latest_row = df.iloc[-1]

    summary.update(
        {
            "duration_minutes": int(duration.total_seconds() // 60) if pd.notna(duration) else 0,
            "start": start_ts,
            "end": end_ts,
            "latest_mode": latest_row.get("mode"),
            "backend": latest_row.get("backend"),
            "safety_level": latest_row.get("safety_level"),
        }
    )
    return summary
