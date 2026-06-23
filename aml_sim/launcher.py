"""AML-Sim launcher that orchestrates StockSim components directly."""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import sys
import time
from contextlib import contextmanager
from multiprocessing import Process
from pathlib import Path
from typing import Any, Iterator

from aml_sim.runs import AMLRun
from aml_sim.scenario import AMLScenario


def load_env_file(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE pairs from a dotenv-style file."""
    if not path.exists():
        return {}

    values = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    return values


def load_json_file(file_path: str) -> Any:
    """Load JSON config data for StockSim agents that require external orders."""
    if not file_path:
        return {}
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"JSON file '{file_path}' does not exist.")
    with open(file_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


@contextmanager
def temporary_environ(updates: dict[str, str]) -> Iterator[None]:
    """Temporarily apply environment variables for a launched simulation."""
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def temporary_cwd(path: Path) -> Iterator[None]:
    """Temporarily run code from a different working directory."""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def patched_output_directories(
    charts_dir: Path,
    reports_dir: Path,
) -> Iterator[None]:
    """Route StockSim report helpers into the AML run artifact directories."""
    from utils import plot_charts

    original_helper = plot_charts.ensure_output_directories

    def ensure_aml_output_directories() -> tuple[str, str]:
        charts_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        return str(charts_dir), str(reports_dir)

    plot_charts.ensure_output_directories = ensure_aml_output_directories
    try:
        yield
    finally:
        plot_charts.ensure_output_directories = original_helper


def ensure_stocksim_import_path(stocksim_dir: Path) -> None:
    """Make StockSim's package-style absolute imports available to AML-Sim."""
    stocksim_path = str(stocksim_dir)
    if stocksim_path not in sys.path:
        sys.path.insert(0, stocksim_path)


def import_stocksim_components(config: dict[str, Any]) -> dict[str, Any]:
    """Import only the StockSim engine objects required by this scenario."""
    from simulation.simulation_clock import SimulationClock
    from utils.time_utils import interval_to_seconds, parse_datetime_utc

    exchange_mode = config.get("exchange_mode", "orderbook").lower()
    if exchange_mode == "candle":
        from exchanges.candle_based_exchange_agent import CandleBasedExchangeAgent

        exchange_class = CandleBasedExchangeAgent
    else:
        from exchanges.exchange_agent import ExchangeAgent

        exchange_class = ExchangeAgent

    configured_agent_types = {
        agent_details.get("type")
        for agent_details in config.get("agents", {}).values()
    }
    agent_type_mapping = {
        agent_type: import_agent_class(agent_type)
        for agent_type in configured_agent_types
        if agent_type
    }

    return {
        "agent_types": agent_type_mapping,
        "exchange_class": exchange_class,
        "SimulationClock": SimulationClock,
        "interval_to_seconds": interval_to_seconds,
        "parse_datetime_utc": parse_datetime_utc,
    }


def import_agent_class(agent_type: str) -> type:
    """Resolve an AML scenario agent type to an AML-Sim class."""
    if agent_type == "AML_Market_Maker":
        from aml_sim.agents.market_maker_trader import AMLMarketMakerTrader

        return AMLMarketMakerTrader
    if agent_type == "AML_Retail_Trader":
        from aml_sim.agents.retail_trader import AMLRetailTrader

        return AMLRetailTrader
    if agent_type == "AML_Institutional_Trader":
        from aml_sim.agents.institutional_trader import AMLInstitutionalTrader

        return AMLInstitutionalTrader

    raise ValueError(f"Unsupported agent type '{agent_type}'")


def agent_runner(agent_class: type, parameters: dict[str, Any]) -> None:
    """Run one trading agent process."""

    async def async_agent_runner() -> None:
        agent = agent_class(**parameters)
        await agent.initialize()
        await agent.run()

    asyncio.run(async_agent_runner())


def exchange_agent_runner(agent_class: type, parameters: dict[str, Any]) -> None:
    """Run one exchange agent process."""

    async def async_exchange_agent_runner() -> None:
        agent = agent_class(**parameters)
        await agent.initialize()
        await agent.run()

    asyncio.run(async_exchange_agent_runner())


def simulation_clock_runner(
    simulation_config: dict[str, Any],
    rabbitmq_host: str,
    expected_responses: int,
) -> None:
    """Run the StockSim simulation clock process."""
    from simulation.simulation_clock import SimulationClock
    from utils.time_utils import interval_to_seconds, parse_datetime_utc

    tick_interval_raw = simulation_config.get("tick_interval", "1d")
    if isinstance(tick_interval_raw, str):
        tick_interval_seconds = interval_to_seconds(tick_interval_raw)
    else:
        tick_interval_seconds = tick_interval_raw

    simulation_clock = SimulationClock(
        start_time=parse_datetime_utc(simulation_config["start_time"]),
        end_time=parse_datetime_utc(simulation_config["end_time"]),
        tick_interval_seconds=tick_interval_seconds,
        rabbitmq_host=rabbitmq_host,
        expected_exchange_agent_count=simulation_config.get("expected_exchange_agent_count", 1),
        expected_responses=expected_responses,
    )
    asyncio.run(simulation_clock.run())


def build_agent_param_customizers(
    interval_to_seconds: Any,
) -> dict[str, Any]:
    """Create parameter transforms for AML-owned agent types."""
    def normalize_aml_agent_params(params: dict[str, Any], default_action_interval: int) -> dict[str, Any]:
        normalized = {
            **params,
            "action_interval_seconds": interval_to_seconds(params["action_interval"])
            if "action_interval" in params
            else params.get("action_interval_seconds", default_action_interval),
        }
        if "slow_loop_interval" in params:
            normalized["slow_loop_interval_seconds"] = interval_to_seconds(
                params["slow_loop_interval"]
            )
        return normalized

    return {
        "AML_Market_Maker": lambda params: normalize_aml_agent_params(params, 60),
        "AML_Retail_Trader": lambda params: normalize_aml_agent_params(params, 60),
        "AML_Institutional_Trader": lambda params: normalize_aml_agent_params(params, 300),
    }


def launch_stocksim(
    scenario: AMLScenario,
    aml_run: AMLRun,
    stocksim_dir: Path,
    env_file: Path,
    generate_reports: bool = False,
) -> int:
    """Launch StockSim engine objects from AML-Sim orchestration code."""
    if not stocksim_dir.exists():
        raise FileNotFoundError(f"StockSim directory not found: {stocksim_dir}")

    env_updates = load_env_file(env_file)
    env_updates["LOG_DIR"] = str(aml_run.logs_dir)
    env_updates["METRICS_OUTPUT_DIR"] = str(aml_run.reports_dir / "agents")
    if scenario.rabbitmq_host:
        env_updates["RABBITMQ_HOST"] = scenario.rabbitmq_host

    print(f"Launching StockSim components from AML-Sim")
    print(f"StockSim component root: {stocksim_dir}")
    print(f"Generated config: {aml_run.stocksim_config_path}")
    print(f"AML env file: {env_file if env_file.exists() else 'not found'}")
    print(f"Logs: {aml_run.logs_dir}")

    ensure_stocksim_import_path(stocksim_dir)
    with temporary_environ(env_updates), temporary_cwd(stocksim_dir):
        return run_stocksim_components(
            config=scenario.stocksim_config,
            aml_run=aml_run,
            generate_reports=generate_reports,
        )


def run_stocksim_components(
    config: dict[str, Any],
    aml_run: AMLRun,
    generate_reports: bool = False,
) -> int:
    """Start exchange, trader, and clock processes using StockSim classes."""
    components = import_stocksim_components(config)
    interval_to_seconds = components["interval_to_seconds"]
    agent_type_mapping = components["agent_types"]
    exchange_mode = config.get("exchange_mode", "orderbook").lower()

    instruments = config.get("instruments", [])
    exchanges_config = config.get("exchanges", {})
    agents_config = config.get("agents", {})
    simulation_config = config.get("simulation", {})
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "localhost")

    simulation_start_time = simulation_config["start_time"]
    simulation_end_time = simulation_config["end_time"]

    indicator_kwargs_map = {
        instrument: inst_cfg.get("indicator_kwargs", {})
        for instrument, inst_cfg in exchanges_config.items()
    }
    warmup_candles_map = {
        instrument: inst_cfg.get("warmup_candles", 250)
        for instrument, inst_cfg in exchanges_config.items()
    }
    print(f"Exchange mode: {exchange_mode}")
    print(f"Instruments: {', '.join(instruments)}")
    print(f"Configured agent groups: {len(agents_config)}")

    exchange_agents = start_exchange_processes(
        exchange_mode=exchange_mode,
        instruments=instruments,
        exchanges_config=exchanges_config,
        simulation_start_time=simulation_start_time,
        simulation_end_time=simulation_end_time,
        rabbitmq_host=rabbitmq_host,
        indicator_kwargs_map=indicator_kwargs_map,
        warmup_candles_map=warmup_candles_map,
        exchange_class=components["exchange_class"],
    )

    print("Waiting for exchange agents to initialize...")
    time.sleep(10)

    instrument_exchange_map = build_instrument_exchange_map(exchange_mode, instruments)
    agent_custom_params = build_agent_param_customizers(
        interval_to_seconds=interval_to_seconds
    )
    agent_processes = start_trader_processes(
        agents_config=agents_config,
        agent_type_mapping=agent_type_mapping,
        agent_custom_params=agent_custom_params,
        instrument_exchange_map=instrument_exchange_map,
        rabbitmq_host=rabbitmq_host,
    )

    print("Waiting for trading agents to initialize...")
    time.sleep(20)

    llm_count = sum(
        details.get("count", 1)
        for details in agents_config.values()
        if details.get("type") == "LLMTradingAgent"
    )
    clock_process = Process(
        target=simulation_clock_runner,
        args=(simulation_config, rabbitmq_host, llm_count),
        name="SimulationClock",
    )
    clock_process.start()
    print("Started SimulationClock.")

    all_processes = exchange_agents + agent_processes + [clock_process]

    def shutdown(signum: int, _frame: Any) -> None:
        print(f"Received shutdown signal {signum}. Terminating simulation processes...")
        terminate_processes(all_processes)
        raise SystemExit(0)

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    exit_code = 0
    try:
        for process in all_processes:
            process.join()
    except KeyboardInterrupt:
        terminate_processes(all_processes)
        exit_code = 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    if exit_code == 0:
        generate_aml_reports(aml_run)
        if generate_reports:
            generate_stocksim_reports(config, aml_run)

    return exit_code


def generate_aml_reports(aml_run: AMLRun) -> None:
    """Generate AML-owned reports from run-local agent artifacts."""
    from aml_sim.reporting import generate_trader_action_report

    generate_trader_action_report(aml_run.reports_dir / "agents", aml_run.reports_dir)


def generate_stocksim_reports(config: dict[str, Any], aml_run: AMLRun) -> None:
    """Call StockSim's report generator while saving outputs under AML-Sim."""
    print(f"Generating StockSim reports in: {aml_run.reports_dir}")
    with patched_output_directories(aml_run.charts_dir, aml_run.reports_dir):
        from aml_sim.reporting import generate_post_simulation_artifacts

        generate_post_simulation_artifacts(config)


def build_instrument_exchange_map(exchange_mode: str, instruments: list[str]) -> dict[str, str]:
    """Map instrument symbols to the exchange agent ids AML-Sim will start."""
    if exchange_mode == "candle":
        return {instrument: f"candle_exchange_{instrument.lower()}" for instrument in instruments}
    return {instrument: f"exchange_{instrument.lower()}" for instrument in instruments}


def start_exchange_processes(
    exchange_mode: str,
    instruments: list[str],
    exchanges_config: dict[str, Any],
    simulation_start_time: str,
    simulation_end_time: str,
    rabbitmq_host: str,
    indicator_kwargs_map: dict[str, Any],
    warmup_candles_map: dict[str, Any],
    exchange_class: type,
) -> list[Process]:
    """Start one StockSim exchange process per configured instrument."""
    exchange_processes = []
    instrument_exchange_map = build_instrument_exchange_map(exchange_mode, instruments)

    for instrument, exchange_id in instrument_exchange_map.items():
        inst_cfg = exchanges_config.get(instrument, {})

        if exchange_mode == "candle":
            exchange_params = {
                "instrument": instrument,
                "resolution": inst_cfg.get("candle_interval", "1d"),
                "start_date": simulation_start_time,
                "end_date": simulation_end_time,
                "warmup_candles": warmup_candles_map[instrument],
                "agent_id": exchange_id,
                "rabbitmq_host": rabbitmq_host,
                "tickers": inst_cfg.get("news", {}).get("tickers", [instrument]),
                "spread_factor": inst_cfg.get("spread_factor", 0.001),
                "limit_news": inst_cfg.get("news", {}).get("max_results", 50),
                "indicator_kwargs": indicator_kwargs_map[instrument],
                "data_source": inst_cfg.get("data_source", "polygon").lower(),
                "symbol_type": inst_cfg.get("symbol_type", "stock"),
            }
        else:
            exchange_params = {
                "instrument": instrument,
                "agent_id": exchange_id,
                "rabbitmq_host": rabbitmq_host,
                "trades_output_file": inst_cfg.get("trades_outfile", ""),
                "tickers": inst_cfg.get("news", {}).get("tickers", [instrument]),
                "limit_news": inst_cfg.get("news", {}).get("max_results", 50),
                "indicator_kwargs": indicator_kwargs_map[instrument],
                "data_source": inst_cfg.get("data_source", "polygon").lower(),
                "symbol_type": inst_cfg.get("symbol_type", "stock"),
                "data_start_date": inst_cfg.get("warmup_start_date"),
                "data_end_date": inst_cfg.get("warmup_end_date") or simulation_end_time,
                "warmup_resolution": inst_cfg.get("warmup_resolution", "1d"),
                "warmup_candles": inst_cfg.get("warmup_candles", 250),
                "resolution": inst_cfg.get("candle_interval", "1m"),
            }

        process = Process(
            target=exchange_agent_runner,
            args=(exchange_class, exchange_params),
            name=exchange_id,
        )
        process.start()
        exchange_processes.append(process)
        print(f"Started exchange '{exchange_id}' for {instrument}.")

    return exchange_processes


def start_trader_processes(
    agents_config: dict[str, Any],
    agent_type_mapping: dict[str, type],
    agent_custom_params: dict[str, Any],
    instrument_exchange_map: dict[str, str],
    rabbitmq_host: str,
) -> list[Process]:
    """Start one process for each configured trader instance."""
    agent_processes = []

    for agent_name, agent_details in agents_config.items():
        agent_type = agent_details.get("type")
        parameters = agent_details.get("parameters", {})
        agent_class = agent_type_mapping.get(agent_type)

        if not agent_class:
            raise ValueError(f"Unsupported agent type '{agent_type}' for agent '{agent_name}'")

        count = agent_details.get("count", 1)
        print(f"Starting {agent_name} ({agent_type}) x {count}")

        for index in range(count):
            unique_agent_id = f"{agent_name}_{index + 1}" if count > 1 else agent_name
            instance_params = dict(parameters)

            if agent_type in agent_custom_params:
                instance_params = agent_custom_params[agent_type](instance_params)

            instance_params["agent_id"] = unique_agent_id
            instance_params.setdefault("instrument_exchange_map", instrument_exchange_map)
            instance_params["rabbitmq_host"] = rabbitmq_host

            if agent_type == "Random_Trader":
                instance_params["seed"] = random.randint(0, 10**6)

            process = Process(
                target=agent_runner,
                args=(agent_class, instance_params),
                name=unique_agent_id,
            )
            process.start()
            agent_processes.append(process)
            print(f"Started trader '{unique_agent_id}'.")

    return agent_processes


def terminate_processes(processes: list[Process]) -> None:
    """Terminate any still-running child processes."""
    for process in processes:
        if process.is_alive():
            process.terminate()
            print(f"Terminated process '{process.name}' (PID: {process.pid}).")
