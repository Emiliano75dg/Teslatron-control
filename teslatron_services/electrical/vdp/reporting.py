"""Generate measurement reports in CSV, JSON, and Markdown formats.

Key functions:
- build_contact_check_report(): Summarize contact-check results with pass/fail status
- build_characterization_report(): Full analysis with sheet resistance, statistics
- write_contact_check_report(): Write CSV/JSON/Markdown to file
- write_characterization_report(): Write CSV/JSON/Markdown to file

Outputs three parallel files:
1. CSV: Raw measurements (timestamp, current, voltage, contacts, status)
2. JSON: Parsed results + analysis (Rs, reciprocity, anisotropy)
3. Markdown: Human-readable summary (contact pairs, anomalies, pass/fail)
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, pstdev

from .analysis import reciprocity_error, rotation_spread, solve_sheet_resistance, vdp_asymmetry
from .runner import MeasurementRecord


@dataclass(frozen=True)
class ContactPairSummary:
    measurement_id: str
    pair: str
    count: int
    iv_point_count: int
    max_abs_current_A: float
    iv_slope_ohm: float
    iv_intercept_V: float
    iv_r2: float
    resistance_mean_ohm: float
    resistance_std_ohm: float
    resistance_rsd: float
    min_resistance_ohm: float
    max_resistance_ohm: float
    linearity_error: float
    compliance_count: int
    pass_status: bool
    flags: tuple[str, ...]


@dataclass(frozen=True)
class ContactCheckReport:
    overall_pass: bool
    total_records: int
    pair_summaries: tuple[ContactPairSummary, ...]


@dataclass(frozen=True)
class CharacterizationReport:
    overall_pass: bool
    total_records: int
    resistance_by_measurement_ohm: dict[str, float]
    r_a_ohm: float | None
    r_b_ohm: float | None
    sheet_resistance_ohm_per_sq: float | None
    sheet_resistance_converged: bool
    sheet_resistance_residual: float | None
    vdp_asymmetry_value: float | None
    rotation_spread_value: float | None
    reciprocity_errors: dict[str, float]
    compliance_count: int
    flags: tuple[str, ...]


def build_contact_check_report(
    records: list[MeasurementRecord],
    *,
    max_rsd: float = 0.05,
    max_linearity_error: float = 0.05,
) -> ContactCheckReport:
    groups: dict[str, list[MeasurementRecord]] = defaultdict(list)
    for record in records:
        groups[record.measurement_id].append(record)

    summaries = tuple(
        _summarize_pair(
            measurement_id,
            pair_records,
            max_rsd=max_rsd,
            max_linearity_error=max_linearity_error,
        )
        for measurement_id, pair_records in sorted(groups.items())
    )
    return ContactCheckReport(
        overall_pass=all(summary.pass_status for summary in summaries),
        total_records=len(records),
        pair_summaries=summaries,
    )


def write_contact_check_report(report: ContactCheckReport, output_csv: Path) -> tuple[Path, Path]:
    stem = output_csv.with_suffix("")
    json_path = stem.with_name(f"{stem.name}_report.json")
    md_path = stem.with_name(f"{stem.name}_report.md")

    json_path.write_text(
        json.dumps(asdict(report), indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_format_contact_report_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_characterization_report(records: list[MeasurementRecord]) -> CharacterizationReport:
    by_id = _mean_resistance_by_id(records)
    compliance_count = sum(
        1 for record in records if record.compliance_positive or record.compliance_negative
    )
    flags: list[str] = []
    if compliance_count:
        flags.append("compliance")

    r_a = _mean_available(by_id, ["R_AB_CD", "R_CD_AB"])
    r_b = _mean_available(by_id, ["R_BC_DA", "R_DA_BC"])

    sheet_value: float | None = None
    sheet_converged = False
    sheet_residual: float | None = None
    asymmetry: float | None = None
    if r_a is not None and r_b is not None and r_a > 0 and r_b > 0:
        solution = solve_sheet_resistance(r_a, r_b)
        sheet_value = solution.value_ohm_per_sq
        sheet_converged = solution.converged
        sheet_residual = solution.residual
        asymmetry = vdp_asymmetry(r_a, r_b)
        if not solution.converged:
            flags.append("sheet_solver")
    else:
        flags.append("missing_or_invalid_vdp_resistance")

    rotation_ids = ["R_AB_CD", "R_BC_DA", "R_CD_AB", "R_DA_BC"]
    rotation_values = [by_id[item] for item in rotation_ids if item in by_id]
    spread = rotation_spread(rotation_values) if len(rotation_values) >= 2 else None

    recips = _reciprocity_errors(by_id)
    return CharacterizationReport(
        overall_pass=not flags,
        total_records=len(records),
        resistance_by_measurement_ohm=by_id,
        r_a_ohm=r_a,
        r_b_ohm=r_b,
        sheet_resistance_ohm_per_sq=sheet_value,
        sheet_resistance_converged=sheet_converged,
        sheet_resistance_residual=sheet_residual,
        vdp_asymmetry_value=asymmetry,
        rotation_spread_value=spread,
        reciprocity_errors=recips,
        compliance_count=compliance_count,
        flags=tuple(flags),
    )


def write_characterization_report(
    report: CharacterizationReport, output_csv: Path
) -> tuple[Path, Path]:
    stem = output_csv.with_suffix("")
    json_path = stem.with_name(f"{stem.name}_report.json")
    md_path = stem.with_name(f"{stem.name}_report.md")
    json_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    md_path.write_text(_format_characterization_report_markdown(report), encoding="utf-8")
    return json_path, md_path


def _summarize_pair(
    measurement_id: str,
    records: list[MeasurementRecord],
    *,
    max_rsd: float,
    max_linearity_error: float,
) -> ContactPairSummary:
    resistances = [record.resistance_ohm for record in records]
    iv_fit = _iv_fit(records)
    resistance_mean = mean(resistances)
    resistance_std = pstdev(resistances) if len(resistances) > 1 else 0.0
    denominator = abs(resistance_mean)
    resistance_rsd = resistance_std / denominator if denominator else 0.0
    linearity = _linearity_error(records)
    compliance_count = sum(
        1 for record in records if record.compliance_positive or record.compliance_negative
    )

    flags: list[str] = []
    if compliance_count:
        flags.append("compliance")
    if iv_fit["r2"] < 1.0 - max_linearity_error:
        flags.append("iv_linearity")
    if resistance_rsd > max_rsd:
        flags.append("repeatability")
    if linearity > max_linearity_error:
        flags.append("linearity")

    return ContactPairSummary(
        measurement_id=measurement_id,
        pair=records[0].current_pair if records else "",
        count=len(records),
        iv_point_count=len(records) * 2,
        max_abs_current_A=max(
            max(abs(record.current_measured_positive_A), abs(record.current_measured_negative_A))
            for record in records
        ),
        iv_slope_ohm=iv_fit["slope_ohm"],
        iv_intercept_V=iv_fit["intercept_V"],
        iv_r2=iv_fit["r2"],
        resistance_mean_ohm=resistance_mean,
        resistance_std_ohm=resistance_std,
        resistance_rsd=resistance_rsd,
        min_resistance_ohm=min(resistances),
        max_resistance_ohm=max(resistances),
        linearity_error=linearity,
        compliance_count=compliance_count,
        pass_status=not flags,
        flags=tuple(flags),
    )


def _linearity_error(records: list[MeasurementRecord]) -> float:
    by_current: dict[float, list[float]] = defaultdict(list)
    for record in records:
        by_current[record.current_set_A].append(record.resistance_ohm)

    if len(by_current) <= 1:
        return 0.0

    means = [mean(values) for _, values in sorted(by_current.items())]
    denominator = abs(mean(means))
    if denominator == 0:
        return 0.0
    return (max(means) - min(means)) / denominator


def _iv_fit(records: list[MeasurementRecord]) -> dict[str, float]:
    points = [
        (record.current_measured_positive_A, record.voltage_positive_V) for record in records
    ] + [(record.current_measured_negative_A, record.voltage_negative_V) for record in records]
    if len(points) < 2:
        return {"slope_ohm": 0.0, "intercept_V": 0.0, "r2": 0.0}
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_mean = mean(xs)
    y_mean = mean(ys)
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    if ss_xx == 0:
        return {"slope_ohm": 0.0, "intercept_V": y_mean, "r2": 0.0}
    slope = sum((x - x_mean) * (y - y_mean) for x, y in points) / ss_xx
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return {"slope_ohm": slope, "intercept_V": intercept, "r2": r2}


def _format_contact_report_markdown(report: ContactCheckReport) -> str:
    status = "PASS" if report.overall_pass else "CHECK"
    lines = [
        "# Contact Check Report",
        "",
        f"Overall status: **{status}**",
        f"Total sweeps: {report.total_records}",
        "",
        (
            "| Pair | I-V points | Max |I| (A) | Slope (ohm) | Offset (V) | "
            "R2 | Compliance | Status | Flags |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for summary in report.pair_summaries:
        pair_status = "PASS" if summary.pass_status else "CHECK"
        flags = ", ".join(summary.flags) if summary.flags else "-"
        lines.append(
            "| "
            f"{summary.pair} | "
            f"{summary.iv_point_count} | "
            f"{summary.max_abs_current_A:.6g} | "
            f"{summary.iv_slope_ohm:.6g} | "
            f"{summary.iv_intercept_V:.3e} | "
            f"{summary.iv_r2:.5f} | "
            f"{summary.compliance_count} | "
            f"{pair_status} | "
            f"{flags} |"
        )
    lines.append("")
    lines.append("Checks: linear I-V fit, stable resistance across sweeps, compliance count = 0.")
    return "\n".join(lines) + "\n"


def _mean_resistance_by_id(records: list[MeasurementRecord]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for record in records:
        groups[record.measurement_id].append(record.resistance_ohm)
    return {measurement_id: mean(values) for measurement_id, values in sorted(groups.items())}


def _mean_available(values: dict[str, float], keys: list[str]) -> float | None:
    available = [values[key] for key in keys if key in values]
    if not available:
        return None
    return mean(available)


def _reciprocity_errors(values: dict[str, float]) -> dict[str, float]:
    pairs = [
        ("R_AB_CD", "R_CD_AB"),
        ("R_BC_DA", "R_DA_BC"),
        ("R_AB_DC", "R_DC_AB"),
        ("R_BC_AD", "R_AD_BC"),
    ]
    errors: dict[str, float] = {}
    for first, second in pairs:
        if first in values and second in values:
            errors[f"{first}__{second}"] = reciprocity_error(values[first], values[second])
    return errors


def _format_characterization_report_markdown(report: CharacterizationReport) -> str:
    status = "PASS" if report.overall_pass else "CHECK"
    lines = [
        "# Characterization Report",
        "",
        f"Overall status: **{status}**",
        f"Total records: {report.total_records}",
        "",
        "## Van der Pauw",
        "",
        f"R_A: {_fmt_optional(report.r_a_ohm)} ohm",
        f"R_B: {_fmt_optional(report.r_b_ohm)} ohm",
        f"Sheet resistance: {_fmt_optional(report.sheet_resistance_ohm_per_sq)} ohm/sq",
        f"Solver converged: {report.sheet_resistance_converged}",
        f"Solver residual: {_fmt_optional(report.sheet_resistance_residual)}",
        f"VdP asymmetry: {_fmt_optional(report.vdp_asymmetry_value)}",
        f"Rotation spread: {_fmt_optional(report.rotation_spread_value)}",
        "",
        "## Mean Resistances",
        "",
        "| Measurement | Mean R (ohm) |",
        "| --- | ---: |",
    ]
    for measurement_id, resistance in report.resistance_by_measurement_ohm.items():
        lines.append(f"| {measurement_id} | {resistance:.6g} |")

    lines.extend(["", "## Reciprocity", "", "| Pair | Error |", "| --- | ---: |"])
    if report.reciprocity_errors:
        for pair, error in report.reciprocity_errors.items():
            lines.append(f"| {pair} | {error:.3%} |")
    else:
        lines.append("| - | - |")

    flags = ", ".join(report.flags) if report.flags else "-"
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            f"Compliance count: {report.compliance_count}",
            f"Flags: {flags}",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6g}"
