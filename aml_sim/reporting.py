"""AML-Sim report helpers that reuse StockSim chart/report utilities."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_report_datetime(value: str):
    """Parse scenario datetimes, including common trailing-Z UTC notation."""
    from utils.time_utils import parse_datetime_utc

    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return parse_datetime_utc(value)


def generate_post_simulation_artifacts(config: dict[str, Any]) -> None:
    """
    Generate StockSim-style artifacts from AML-Sim.

    This intentionally lives in AML-Sim so StockSim can remain an engine/library
    dependency. It still reuses StockSim's data clients and chart/report helpers.
    """
    try:
        from utils.alpha_vantage_client import AlphaVantageClient
        from utils.plot_charts import (
            ensure_output_directories,
            generate_demo_report,
            make_chart_dropdown,
        )
        from utils.polygon_client import PolygonClient

        print("Generating StockSim-style post-simulation artifacts...")

        charts_dir, reports_dir = ensure_output_directories()

        instruments = config.get("instruments", [])
        exchanges_config = config.get("exchanges", {})
        simulation_config = config.get("simulation", {})

        simulation_start_str = simulation_config["start_time"]
        simulation_end_str = simulation_config["end_time"]

        for instrument in instruments:
            try:
                inst_cfg = exchanges_config.get(instrument, {})
                data_source = inst_cfg.get("data_source", "polygon").lower()
                symbol_type = inst_cfg.get("symbol_type", "stock")
                interval = inst_cfg.get("candle_interval", "1d")
                indicator_kwargs = inst_cfg.get("indicator_kwargs", {})

                print(f"Generating artifacts for {instrument} ({symbol_type})...")

                if data_source == "synthetic":
                    print(f"Skipping external chart/report generation for synthetic instrument {instrument}.")
                    continue

                client = AlphaVantageClient() if data_source == "alpha_vantage" else PolygonClient()

                if symbol_type == "crypto":
                    candles = client.load_crypto_aggregates(
                        symbol=instrument,
                        interval=interval,
                        start_date=simulation_start_str,
                        end_date=simulation_end_str,
                        market="USD",
                        sort="asc",
                        limit=10000,
                        use_cache=True,
                    )
                else:
                    candles = client.load_aggregates(
                        symbol=instrument,
                        interval=interval,
                        start_date=simulation_start_str,
                        end_date=simulation_end_str,
                        adjusted=True,
                        sort="asc",
                        limit=10000,
                        use_cache=True,
                    )

                if candles:
                    chart_filename = f"{instrument}_demo_chart.html"
                    make_chart_dropdown(
                        candles=candles,
                        instrument=instrument,
                        scales_seconds=[3600, 14400, 86400],
                        out_html=chart_filename,
                        indicator_kwargs=indicator_kwargs,
                        symbol_type=symbol_type,
                    )

                    report = generate_demo_report(instrument, candles, indicator_kwargs)
                    if report:
                        report_filename = os.path.join(reports_dir, f"{instrument}_demo_report.json")
                        with open(report_filename, "w", encoding="utf-8") as handle:
                            json.dump(report, handle, indent=2)
                        print(f"Generated report: {report_filename}")

            except Exception as exc:
                print(f"Failed to generate artifacts for {instrument}: {exc}")
                continue

        summary_report = {
            "simulation_info": {
                "start_time": simulation_start_str,
                "end_time": simulation_end_str,
                "duration_days": (
                    parse_report_datetime(simulation_end_str) - parse_report_datetime(simulation_start_str)
                ).days,
                "instruments": instruments,
                "total_agents": sum(
                    agent_config.get("count", 1)
                    for agent_config in config.get("agents", {}).values()
                ),
                "exchange_mode": config.get("exchange_mode", "candle"),
            },
            "generated_artifacts": {
                "charts_directory": charts_dir,
                "reports_directory": reports_dir,
                "timestamp": datetime.now().isoformat(),
            },
            "research_metrics": {
                "llm_agents": sum(
                    1
                    for agent in config.get("agents", {}).values()
                    if agent.get("type") == "LLMTradingAgent"
                ),
                "benchmark_agents": sum(
                    1
                    for agent in config.get("agents", {}).values()
                    if agent.get("type") != "LLMTradingAgent"
                ),
                "multi_market": len(
                    {
                        exchanges_config.get(instrument, {}).get("symbol_type", "stock")
                        for instrument in instruments
                    }
                )
                > 1,
            },
        }

        summary_file = os.path.join(reports_dir, "simulation_summary.json")
        with open(summary_file, "w", encoding="utf-8") as handle:
            json.dump(summary_report, handle, indent=2)

        print("Post-simulation artifacts generated successfully.")
        print(f"Charts available in: {charts_dir}")
        print(f"Reports available in: {reports_dir}")

    except Exception as exc:
        print(f"Failed to generate post-simulation artifacts: {exc}")
        print(traceback.format_exc())


def generate_trader_action_report(agent_reports_dir: Path, reports_dir: Path) -> None:
    """Combine per-agent AML action ledgers into one report JSON file."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    agent_reports_dir.mkdir(parents=True, exist_ok=True)

    action_files = sorted(agent_reports_dir.glob("trader_actions_*.json"))
    actions: list[dict[str, Any]] = []
    by_agent: dict[str, dict[str, Any]] = {}

    for action_file in action_files:
        agent_id = action_file.stem.removeprefix("trader_actions_")
        try:
            with action_file.open("r", encoding="utf-8") as handle:
                agent_actions = json.load(handle)
        except Exception as exc:
            print(f"Failed to read trader action file {action_file}: {exc}")
            continue

        if not isinstance(agent_actions, list):
            print(f"Skipping trader action file with non-list payload: {action_file}")
            continue

        by_agent[agent_id] = {
            "action_count": len(agent_actions),
            "submitted_orders": sum(
                1 for action in agent_actions if action.get("event_type") == "order_submitted"
            ),
            "rejected_orders": sum(
                1 for action in agent_actions if action.get("event_type") == "order_rejected"
            ),
            "executed_trades": sum(
                1 for action in agent_actions if action.get("event_type") == "trade_executed"
            ),
        }
        actions.extend(agent_actions)

    actions.sort(key=lambda action: (action.get("timestamp") or "", action.get("agent_id") or ""))
    report = {
        "generated_at": datetime.now().isoformat(),
        "source_directory": str(agent_reports_dir),
        "action_count": len(actions),
        "agents": by_agent,
        "actions": actions,
    }

    output_file = reports_dir / "trader_actions.json"
    with output_file.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print(f"Generated AML trader action report: {output_file}")
