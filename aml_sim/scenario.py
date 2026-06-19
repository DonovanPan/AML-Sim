"""Scenario loading and validation for AML-Sim."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_STOCKSIM_CONFIG_KEYS = [
    "exchange_mode",
    "instruments",
    "exchanges",
    "agents",
    "simulation",
]


@dataclass(frozen=True)
class AMLScenario:
    """Parsed AML scenario with the StockSim-compatible config separated out."""

    path: Path
    name: str
    description: str | None
    rabbitmq_host: str | None
    stocksim_config: dict[str, Any]
    raw: dict[str, Any]


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Scenario file must contain a YAML mapping: {path}")

    return data


def build_stocksim_config(scenario_data: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate the StockSim config embedded in an AML scenario."""
    config = scenario_data.get("stocksim_config")
    if not isinstance(config, dict):
        raise ValueError("Scenario must define a 'stocksim_config' mapping")

    missing = [key for key in REQUIRED_STOCKSIM_CONFIG_KEYS if key not in config]
    if missing:
        raise ValueError(f"stocksim_config missing required keys: {', '.join(missing)}")

    return config


def load_scenario(path: Path) -> AMLScenario:
    """Load an AML scenario file and return its parsed representation."""
    scenario_path = path.expanduser().resolve()
    data = load_yaml(scenario_path)
    stocksim_config = build_stocksim_config(data)

    name = data.get("name") or scenario_path.stem
    description = data.get("description")
    rabbitmq_host = data.get("rabbitmq_host")

    if not isinstance(name, str):
        raise ValueError("Scenario 'name' must be a string when provided")
    if description is not None and not isinstance(description, str):
        raise ValueError("Scenario 'description' must be a string when provided")
    if rabbitmq_host is not None and not isinstance(rabbitmq_host, str):
        raise ValueError("Scenario 'rabbitmq_host' must be a string when provided")

    return AMLScenario(
        path=scenario_path,
        name=name,
        description=description,
        rabbitmq_host=rabbitmq_host,
        stocksim_config=stocksim_config,
        raw=data,
    )

