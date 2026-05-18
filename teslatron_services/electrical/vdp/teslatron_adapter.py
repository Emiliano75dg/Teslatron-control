from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
import json

from .config import (
    load_and_validate_instruments,
    load_and_validate_sequences,
    load_routing_config,
)
from .instruments import B2902B, DAQ6510
from .planner import build_plan
from .reporting import build_characterization_report
from .runner import ContactCheckRunner, MeasurementCancelled, characterization_steps
from .scpi import DryRunTransport, SocketTransport


def run_vdp_characterization_for_teslatron(
    *,
    config,
    run_id: str,
    output_dir: Path,
    cryostat_snapshot_getter,
    stop_requested,
) -> dict[str, Any]:
    instruments_path = Path(config.instruments_config)
    sequences_path = Path(config.measurement_sequences_config)
    routing_path = Path(config.routing_config)
    wiring_path = Path(config.wiring_config) if config.wiring_config else None

    instruments_model = load_and_validate_instruments(instruments_path)
    sequences_model = load_and_validate_sequences(sequences_path)
    routing_config = load_routing_config(routing_path, wiring_path)
    plan = build_plan(
        measurement_config=sequences_model.model_dump(mode="python"),
        routing_config=routing_config,
        include_hall=bool(config.include_hall),
    )
    steps = characterization_steps(plan)

    run_dir = output_dir / _slug(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / f"{run_id}_vdp.csv"
    report_json_path = run_dir / f"{run_id}_vdp_report.json"
    report_md_path = run_dir / f"{run_id}_vdp_report.md"

    transports = []
    try:
        b2902b, daq6510 = _build_instruments(
            instruments_model.model_dump(mode="python"),
            execute=bool(config.execute),
            transports=transports,
        )
        runner = ContactCheckRunner(
            b2902b=b2902b,
            daq6510=daq6510,
            settling_time_s=float(sequences_model.defaults.settling_time_s),
            compliance_v=float(instruments_model.instruments["b2902b"]["compliance_V"]),
            nplc=float(instruments_model.instruments["b2902b"]["nplc"]),
            remote_sense=bool(sequences_model.defaults.characterization_remote_sense),
            sleep_enabled=True,
            stop_requested=stop_requested,
        )
        records = runner.run(steps, csv_path)
        status = "completed"
        error = None
    except MeasurementCancelled as exc:
        records = []
        status = "stopped"
        error = str(exc)
        _ensure_csv_exists(csv_path)
    finally:
        for transport in transports:
            transport.close()

    cryostat_snapshot = _safe_snapshot(cryostat_snapshot_getter)
    report = build_characterization_report(records)
    report_payload = asdict(report)
    report_payload["kind"] = "vdp_characterization"
    report_payload["run_id"] = run_id
    report_payload["status"] = status
    report_payload["cryostat_snapshot"] = cryostat_snapshot
    if error is not None:
        report_payload["error"] = error
    report_json_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    report_md_path.write_text(_markdown_with_status(report, status, cryostat_snapshot, error), encoding="utf-8")

    return {
        "kind": "vdp_characterization",
        "records": [asdict(record) for record in records],
        "csv_path": str(csv_path),
        "report_json_path": str(report_json_path),
        "report_md_path": str(report_md_path),
        "status": status,
        "sheet_resistance_ohm_per_sq": report.sheet_resistance_ohm_per_sq,
        "sheet_resistance_converged": report.sheet_resistance_converged,
        "flags": list(report.flags),
        "cryostat_snapshot": cryostat_snapshot,
    }


def _build_instruments(
    instruments_config: dict[str, Any],
    *,
    execute: bool,
    transports: list[Any],
) -> tuple[B2902B, DAQ6510]:
    transport_defaults = instruments_config["transport"]
    b2902b_cfg = instruments_config["instruments"]["b2902b"]
    daq6510_cfg = instruments_config["instruments"]["daq6510"]
    if execute:
        b_transport = SocketTransport(
            b2902b_cfg["ip_address"],
            int(b2902b_cfg["port"]),
            timeout_s=float(transport_defaults["default_timeout_s"]),
            termination=str(transport_defaults["command_termination"]),
            read_buffer_bytes=int(transport_defaults["read_buffer_bytes"]),
        )
        d_transport = SocketTransport(
            daq6510_cfg["ip_address"],
            int(daq6510_cfg["port"]),
            timeout_s=float(transport_defaults["default_timeout_s"]),
            termination=str(transport_defaults["command_termination"]),
            read_buffer_bytes=int(transport_defaults["read_buffer_bytes"]),
        )
    else:
        b_transport = DryRunTransport(name="B2902B")
        d_transport = DryRunTransport(name="DAQ6510")
    transports.extend([b_transport, d_transport])
    return (
        B2902B(transport=b_transport, channel=int(b2902b_cfg["channel"])),
        DAQ6510(transport=d_transport),
    )


def _safe_snapshot(getter: Callable[[], Any]) -> Any:
    try:
        return getter()
    except Exception as exc:
        return {"error": str(exc)}


def _markdown_with_status(report, status: str, cryostat_snapshot: Any, error: str | None) -> str:
    lines = [
        "# VdP Characterization Report",
        "",
        f"Status: **{status.upper()}**",
        f"Total records: {report.total_records}",
        f"Sheet resistance (ohm/sq): {report.sheet_resistance_ohm_per_sq!r}",
        "",
        "## Flags",
    ]
    if report.flags:
        lines.extend(f"- {flag}" for flag in report.flags)
    else:
        lines.append("- none")
    if error is not None:
        lines.extend(["", "## Error", error])
    lines.extend(
        [
            "",
            "## Cryostat Snapshot",
            "```json",
            json.dumps(cryostat_snapshot, indent=2, default=str),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _ensure_csv_exists(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


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
