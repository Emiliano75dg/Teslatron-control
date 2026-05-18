"""Configuration file loading and validation.

This module handles YAML configuration loading with Pydantic-based validation.
Functions support both raw dict loading (backward compatible) and validated
model loading (recommended for new code).

Key functions:
    load_yaml() - Load YAML file as dict (no validation)
    load_and_validate_instruments() - Load and validate instruments.yaml
    load_and_validate_sequences() - Load and validate measurement_sequences.yaml
    load_and_validate_routing() - Load and validate routing_template.yaml
    validate_all_configs() - Validate all three configs together
    load_routing_config() - Load routing with optional wiring override
    deep_merge() - Recursively merge dicts for config overlays
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .config_schemas import (
    InstrumentsRootConfig,
    MeasurementSequencesRootConfig,
    RoutingTemplateRootConfig,
)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load YAML file and return as dict without validation.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML as dict.

    Raises:
        ValueError: If file does not contain a mapping.
        FileNotFoundError: If file does not exist.
        yaml.YAMLError: If YAML is malformed.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a mapping")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dict into base dict.

    Used to apply wiring overlays on top of routing templates.

    Args:
        base: Base configuration dict.
        override: Override dict to merge in.

    Returns:
        Merged dict with overrides applied to nested dicts.
    """
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_and_validate_instruments(path: str | Path) -> InstrumentsRootConfig:
    """Load and validate instruments.yaml against Pydantic schema.

    Args:
        path: Path to instruments.yaml.

    Returns:
        Validated InstrumentsRootConfig model.

    Raises:
        ValidationError:
            If config does not match schema
            (missing fields, wrong types, invalid values).
        FileNotFoundError: If file does not exist.
        yaml.YAMLError: If YAML is malformed.
    """
    raw = load_yaml(path)
    try:
        return InstrumentsRootConfig(**raw)
    except ValidationError as e:
        raise ValueError(f"Invalid instruments.yaml: {e}") from e


def load_and_validate_sequences(path: str | Path) -> MeasurementSequencesRootConfig:
    """Load and validate measurement_sequences.yaml against Pydantic schema.

    Args:
        path: Path to measurement_sequences.yaml.

    Returns:
        Validated MeasurementSequencesRootConfig model.

    Raises:
        ValidationError: If config does not match schema (missing currents, invalid contacts, etc.).
        FileNotFoundError: If file does not exist.
        yaml.YAMLError: If YAML is malformed.
    """
    raw = load_yaml(path)
    try:
        return MeasurementSequencesRootConfig(**raw)
    except ValidationError as e:
        raise ValueError(f"Invalid measurement_sequences.yaml: {e}") from e


def load_and_validate_routing(path: str | Path) -> RoutingTemplateRootConfig:
    """Load and validate routing_template.yaml against Pydantic schema.

    Args:
        path: Path to routing_template.yaml.

    Returns:
        Validated RoutingTemplateRootConfig model.

    Raises:
        ValidationError: If config does not match schema (invalid matrix, missing rows, etc.).
        FileNotFoundError: If file does not exist.
        yaml.YAMLError: If YAML is malformed.
    """
    raw = load_yaml(path)
    try:
        return RoutingTemplateRootConfig(**raw)
    except ValidationError as e:
        raise ValueError(f"Invalid routing_template.yaml: {e}") from e


def validate_routing_dict(
    routing: dict[str, Any], *, label: str = "routing config"
) -> RoutingTemplateRootConfig:
    """Validate an already-loaded routing dict.

    This is used after applying wiring overlays, where validating only the
    base template would miss invalid lab-specific row/column assignments.
    """
    try:
        return RoutingTemplateRootConfig(**routing)
    except ValidationError as e:
        raise ValueError(f"Invalid {label}: {e}") from e


def validate_all_configs(
    instruments_path: str | Path,
    sequences_path: str | Path,
    routing_path: str | Path,
) -> tuple[InstrumentsRootConfig, MeasurementSequencesRootConfig, RoutingTemplateRootConfig]:
    """Load and validate all three configuration files.

    Call this at startup before any measurements to catch config errors early
    (instead of failing mid-measurement with confusing SCPI errors).

    Args:
        instruments_path: Path to instruments.yaml.
        sequences_path: Path to measurement_sequences.yaml.
        routing_path: Path to routing_template.yaml.

    Returns:
        Tuple of (instruments_config, sequences_config, routing_config) models.

    Raises:
        ValueError: If any config is invalid (detailed error message with field and issue).
        FileNotFoundError: If any file does not exist.
        yaml.YAMLError: If any YAML is malformed.
    """
    instruments = load_and_validate_instruments(instruments_path)
    sequences = load_and_validate_sequences(sequences_path)
    routing = load_and_validate_routing(routing_path)
    return instruments, sequences, routing


def load_routing_config(
    routing_path: str | Path,
    wiring_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load routing config with optional wiring overlay as dict (backward compatible).

    This function returns raw dicts instead of validated models.
    For new code, prefer load_and_validate_routing().

    Args:
        routing_path: Path to routing_template.yaml.
        wiring_path: Optional path to wiring.yaml with routing overrides.

    Returns:
        Merged routing config as dict.

    Raises:
        ValueError: If YAML files are invalid.
        FileNotFoundError: If files do not exist.
    """
    routing = load_yaml(routing_path)
    if wiring_path is None:
        return routing

    wiring = load_yaml(wiring_path)
    overrides = wiring.get("routing_overrides", wiring)
    if not isinstance(overrides, dict):
        raise ValueError(f"{wiring_path} does not contain routing overrides")
    merged = deep_merge(routing, overrides)
    validate_routing_dict(
        merged, label=f"merged routing config from {routing_path} and {wiring_path}"
    )
    return merged
