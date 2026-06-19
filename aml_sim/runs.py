"""Run directory creation and artifact writing for AML-Sim."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aml_sim.scenario import AMLScenario


@dataclass(frozen=True)
class AMLRun:
    """Filesystem locations for one AML-Sim run."""

    run_id: str
    run_dir: Path
    logs_dir: Path
    charts_dir: Path
    reports_dir: Path
    scenario_path: Path
    stocksim_config_path: Path
    metadata_path: Path


def make_run_id(scenario: AMLScenario, run_id: str | None) -> str:
    """Create a stable caller-provided run id or a timestamped default."""
    if run_id:
        return run_id

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{scenario.path.stem}_{timestamp}"


def write_yaml(data: dict[str, Any], path: Path) -> None:
    """Write YAML using the repo's existing simple YAML style."""
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def create_run(scenario: AMLScenario, runs_dir: Path, run_id: str | None = None) -> AMLRun:
    """Create a run directory and write reproducibility artifacts."""
    final_run_id = make_run_id(scenario, run_id)
    runs_dir = runs_dir.expanduser().resolve()
    run_dir = runs_dir / final_run_id
    logs_dir = run_dir / "logs"
    charts_dir = run_dir / "charts"
    reports_dir = run_dir / "reports"
    scenario_path = run_dir / "scenario.yaml"
    stocksim_config_path = run_dir / "stocksim_config.yaml"
    metadata_path = run_dir / "metadata.json"

    run_dir.mkdir(parents=True, exist_ok=False)
    logs_dir.mkdir()
    charts_dir.mkdir()
    reports_dir.mkdir()

    write_yaml(scenario.raw, scenario_path)
    write_yaml(scenario.stocksim_config, stocksim_config_path)
    write_metadata(
        scenario=scenario,
        run_id=final_run_id,
        run_dir=run_dir,
        logs_dir=logs_dir,
        charts_dir=charts_dir,
        reports_dir=reports_dir,
        scenario_path=scenario_path,
        stocksim_config_path=stocksim_config_path,
        metadata_path=metadata_path,
    )

    return AMLRun(
        run_id=final_run_id,
        run_dir=run_dir,
        logs_dir=logs_dir,
        charts_dir=charts_dir,
        reports_dir=reports_dir,
        scenario_path=scenario_path,
        stocksim_config_path=stocksim_config_path,
        metadata_path=metadata_path,
    )


def write_metadata(
    scenario: AMLScenario,
    run_id: str,
    run_dir: Path,
    logs_dir: Path,
    charts_dir: Path,
    reports_dir: Path,
    scenario_path: Path,
    stocksim_config_path: Path,
    metadata_path: Path,
) -> None:
    """Write the first AML-Sim run metadata record."""
    metadata = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": {
            "name": scenario.name,
            "description": scenario.description,
            "source_path": str(scenario.path),
            "archived_path": str(scenario_path),
            "rabbitmq_host": scenario.rabbitmq_host,
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "logs_dir": str(logs_dir),
            "charts_dir": str(charts_dir),
            "reports_dir": str(reports_dir),
            "stocksim_config_path": str(stocksim_config_path),
            "metadata_path": str(metadata_path),
        },
        "stocksim": {
            "launch_mode": "aml_component_orchestrator",
        },
    }

    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")
