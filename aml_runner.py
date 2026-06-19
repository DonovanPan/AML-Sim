"""AML-level runner for StockSim scenarios.

This script keeps AML orchestration outside the StockSim fork. It reads an AML
scenario file, writes the StockSim-compatible config into a run directory, and
optionally launches StockSim with that generated config.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from aml_sim.launcher import launch_stocksim
from aml_sim.runs import create_run
from aml_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parent
STOCKSIM_DIR = ROOT / "simulators" / "StockSim"
DEFAULT_SCENARIO = ROOT / "scenarios" / "aml_orderbook_replay.yaml"
RUNS_DIR = ROOT / ".aml_runs"
ENV_FILE = ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an AML scenario through StockSim.")
    parser.add_argument(
        "scenario",
        nargs="?",
        default=str(DEFAULT_SCENARIO),
        help="Path to an AML scenario YAML file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the StockSim config but do not launch StockSim.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run directory name under .aml_runs/.",
    )
    parser.add_argument(
        "--reports",
        action="store_true",
        help="Generate StockSim post-simulation reports into the AML run directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenario = load_scenario(Path(args.scenario))
    aml_run = create_run(scenario, RUNS_DIR, args.run_id)

    print(f"Scenario: {scenario.name}")
    print(f"Run directory: {aml_run.run_dir}")
    print(f"Scenario archived: {aml_run.scenario_path}")
    print(f"StockSim config written: {aml_run.stocksim_config_path}")
    print(f"Run metadata written: {aml_run.metadata_path}")

    if args.dry_run:
        print("Dry run only. StockSim was not launched.")
        return 0

    return launch_stocksim(
        scenario,
        aml_run,
        STOCKSIM_DIR,
        ENV_FILE,
        generate_reports=args.reports,
    )


if __name__ == "__main__":
    raise SystemExit(main())
