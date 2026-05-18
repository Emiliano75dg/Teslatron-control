from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass(slots=True)
class CryostatEndpointConfig:
    state_url: str = "http://127.0.0.1:8765/state"
    recipe_signal_url: str = "http://127.0.0.1:8765/recipes/signal"
    timeout_s: float = 2.0
    poll_interval_s: float = 1.0
    stale_after_s: float = 5.0


@dataclass(slots=True)
class MeasurementSessionConfig:
    save_dir: str = "data/electrical"


@dataclass(slots=True)
class MockInstrumentConfig:
    base_value: float = 1e-9
    noise_fraction: float = 0.05
    unit: str = "A"


@dataclass(slots=True)
class InstrumentConfig:
    driver: str = "mock"
    address: str = "MOCK::INSTR"
    mock: MockInstrumentConfig = field(default_factory=MockInstrumentConfig)


@dataclass(slots=True)
class PlanTriggerConfig:
    type: str = "recipe_signal"
    signal: str = ""


@dataclass(slots=True)
class PlanCompletionConfig:
    notify_recipe: bool = False
    success_signal: str | None = None
    failure_signal: str | None = None


@dataclass(slots=True)
class VdpConfig:
    instruments_config: str = "config/vdp_instruments.example.yaml"
    measurement_sequences_config: str = "config/vdp_measurement_sequences.yaml"
    routing_config: str = "config/vdp_routing_template.yaml"
    wiring_config: str | None = "config/vdp_wiring.example.yaml"
    execute: bool = False
    include_contact_check: bool = False
    include_hall: bool = False


@dataclass(slots=True)
class MeasurementStepConfig:
    instrument: str
    action: str = "measure"


@dataclass(slots=True)
class MeasurementPlanConfig:
    id: str
    mode: str = "command-driven"
    trigger: PlanTriggerConfig = field(default_factory=PlanTriggerConfig)
    steps: list[MeasurementStepConfig] = field(default_factory=list)
    require_safe_to_measure: bool = True
    completion: PlanCompletionConfig = field(default_factory=PlanCompletionConfig)


@dataclass(slots=True)
class ElectricalServiceConfig:
    read_only: bool = False
    cryostat: CryostatEndpointConfig = field(default_factory=CryostatEndpointConfig)
    measurement_session: MeasurementSessionConfig = field(default_factory=MeasurementSessionConfig)
    instruments: dict[str, InstrumentConfig] = field(default_factory=dict)
    vdp: VdpConfig = field(default_factory=VdpConfig)
    plans: dict[str, MeasurementPlanConfig] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: str | Path | None = None) -> ElectricalServiceConfig:
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


def config_from_mapping(data: dict[str, Any]) -> ElectricalServiceConfig:
    electrical = data.get("electrical", data)
    instruments = {
        name: InstrumentConfig(
            driver=(item or {}).get("driver", "mock"),
            address=(item or {}).get("address", "MOCK::INSTR"),
            mock=MockInstrumentConfig(**((item or {}).get("mock", {}) or {})),
        )
        for name, item in (electrical.get("instruments", {}) or {}).items()
    }
    if not instruments:
        instruments = {"mock_meter": InstrumentConfig()}
    plans = _plans_from_mapping(electrical.get("plans", []) or [])
    return ElectricalServiceConfig(
        read_only=bool(electrical.get("read_only", False)),
        cryostat=CryostatEndpointConfig(**(electrical.get("cryostat", {}) or {})),
        measurement_session=MeasurementSessionConfig(
            **(electrical.get("measurement_session", {}) or {})
        ),
        instruments=instruments,
        vdp=VdpConfig(**(electrical.get("vdp", {}) or {})),
        plans=plans,
    )


def _plans_from_mapping(raw_plans: list[dict[str, Any]]) -> dict[str, MeasurementPlanConfig]:
    plans: dict[str, MeasurementPlanConfig] = {}
    for item in raw_plans:
        if not isinstance(item, dict):
            raise ValueError("Electrical plans must be objects")
        plan_id = str(item.get("id") or "").strip()
        if not plan_id:
            raise ValueError("Electrical plan id cannot be empty")
        if plan_id in plans:
            raise ValueError(f"Duplicate electrical plan id: {plan_id}")
        trigger_data = item.get("trigger", {}) or {}
        completion_data = item.get("completion", {}) or {}
        steps_data = item.get("steps", []) or []
        if not isinstance(steps_data, list) or not steps_data:
            raise ValueError(f"Electrical plan {plan_id!r} must define at least one step")
        steps = []
        for step in steps_data:
            if not isinstance(step, dict):
                raise ValueError(f"Electrical plan {plan_id!r} steps must be objects")
            instrument = str(step.get("instrument") or "").strip()
            action = str(step.get("action") or "measure").strip() or "measure"
            if not instrument:
                raise ValueError(f"Electrical plan {plan_id!r} step instrument cannot be empty")
            if action not in {"measure", "vdp_characterization"}:
                raise ValueError(
                    f"Electrical plan {plan_id!r} step action {action!r} is not supported"
                )
            steps.append(MeasurementStepConfig(instrument=instrument, action=action))
        mode = str(item.get("mode") or "command-driven").strip() or "command-driven"
        if mode not in {"command-driven", "continuous"}:
            raise ValueError(f"Electrical plan {plan_id!r} has unsupported mode {mode!r}")
        trigger_type = str(trigger_data.get("type") or "recipe_signal").strip() or "recipe_signal"
        if trigger_type not in {"recipe_signal", "manual", "interval"}:
            raise ValueError(
                f"Electrical plan {plan_id!r} has unsupported trigger type {trigger_type!r}"
            )
        signal = str(trigger_data.get("signal") or "").strip()
        if trigger_type == "recipe_signal" and not signal:
            raise ValueError(f"Electrical plan {plan_id!r} recipe_signal trigger requires a signal")
        plans[plan_id] = MeasurementPlanConfig(
            id=plan_id,
            mode=mode,
            trigger=PlanTriggerConfig(type=trigger_type, signal=signal),
            steps=steps,
            require_safe_to_measure=bool(item.get("require_safe_to_measure", True)),
            completion=PlanCompletionConfig(
                notify_recipe=bool(completion_data.get("notify_recipe", False)),
                success_signal=completion_data.get("success_signal"),
                failure_signal=completion_data.get("failure_signal"),
            ),
        )
    return plans
