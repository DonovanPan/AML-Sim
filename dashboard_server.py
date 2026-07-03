"""Local dashboard server with a small API for launching AML simulations."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml


ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = Path("scenarios/aml_one_hour_live.yaml")
DEFAULT_RUN_ID = "local_multi_agent"
DOCKER_COMPOSE_FILE = ROOT / "simulators" / "StockSim" / "docker-compose.yml"
RUNS_DIR = ROOT / ".aml_runs"


@dataclass
class RunJob:
    run_id: str
    scenario: str
    status: str = "starting"
    command: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    return_code: int | None = None
    log_lines: list[str] = field(default_factory=list)
    error: str | None = None

    def append_log(self, line: str) -> None:
        self.log_lines.append(line.rstrip())
        if len(self.log_lines) > 400:
            del self.log_lines[:-400]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "status": self.status,
            "command": self.command,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "error": self.error,
            "log_tail": self.log_lines[-40:],
            "dashboard_url": f"/dashboard.html?run={self.run_id}",
        }


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current_job: RunJob | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            if self.current_job is None:
                return {"status": "idle"}
            return self.current_job.to_dict()

    def start_job(self, scenario: str, run_id: str, reports: bool) -> RunJob:
        with self.lock:
            if self.current_job and self.current_job.status == "running":
                raise RuntimeError(
                    f"Run {self.current_job.run_id!r} is already in progress."
                )

            scenario_path = validate_scenario_path(scenario)
            clean_run_id = next_available_run_id(validate_run_id(run_id))
            command = [
                runner_python(),
                str(ROOT / "aml_runner.py"),
                str(scenario_path),
                "--run-id",
                clean_run_id,
            ]
            if reports:
                command.append("--reports")

            job = RunJob(
                run_id=clean_run_id,
                scenario=str(scenario_path.relative_to(ROOT)),
                status="running",
                command=command,
            )
            self.current_job = job

        thread = threading.Thread(
            target=run_simulation_process,
            args=(job,),
            daemon=True,
        )
        thread.start()
        return job


STATE = DashboardState()


def validate_run_id(run_id: str | None) -> str:
    value = (run_id or DEFAULT_RUN_ID).strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not value or any(char not in allowed for char in value):
        raise ValueError("run_id may only contain letters, numbers, '_' and '-'.")
    return value


def validate_scenario_path(scenario: str | None) -> Path:
    value = (scenario or str(DEFAULT_SCENARIO)).strip()
    path = (ROOT / value).resolve()
    if ROOT not in path.parents and path != ROOT:
        raise ValueError("scenario must be inside the AML-Sim workspace.")
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("scenario must be a YAML file.")
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {path}")
    return path


def next_available_run_id(run_id: str) -> str:
    candidate = run_id
    suffix = 2
    while (RUNS_DIR / candidate).exists():
        candidate = f"{run_id}_{suffix}"
        suffix += 1
    return candidate


def runner_python() -> str:
    """Prefer the repo venv so dashboard launches use installed AML deps."""
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def run_simulation_process(job: RunJob) -> None:
    try:
        process = subprocess.Popen(
            job.command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            job.append_log(line)
        job.return_code = process.wait()
        job.finished_at = time.time()
        job.status = "completed" if job.return_code == 0 else "failed"
        if job.return_code != 0:
            job.error = f"aml_runner.py exited with code {job.return_code}"
    except Exception as exc:  # pragma: no cover - defensive local server path
        job.status = "failed"
        job.finished_at = time.time()
        job.error = str(exc)
        job.append_log(str(exc))


def rabbitmq_reachable(
    host: str = "127.0.0.1",
    port: int = 5672,
    timeout_seconds: float = 1.0,
) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def load_run_artifacts(run_id: str | None) -> dict[str, Any]:
    clean_run_id = validate_run_id(run_id)
    run_dir = RUNS_DIR / clean_run_id
    current_job = STATE.snapshot()
    payload: dict[str, Any] = {
        "run_id": clean_run_id,
        "exists": run_dir.exists(),
        "missing": [],
        "actions_report": None,
        "summary": None,
        "metadata": None,
        "configured_agents": [],
        "agent_reports": {},
        "order_log": "",
        "shock_log": "",
        "job": current_job if current_job.get("run_id") == clean_run_id else {"status": "idle"},
    }
    if not run_dir.exists():
        return payload

    payload["metadata"] = read_json_if_exists(run_dir / "metadata.json", payload)
    payload["configured_agents"] = load_configured_agents(run_dir)
    reports_dir = run_dir / "reports"
    logs_dir = run_dir / "logs"
    payload["actions_report"] = read_json_if_exists(
        reports_dir / "trader_actions.json",
        payload,
    )
    payload["summary"] = read_json_if_exists(
        reports_dir / "simulation_summary.json",
        payload,
    )
    payload["agent_reports"] = load_agent_reports(reports_dir / "agents")

    instruments = (
        payload.get("summary", {})
        .get("simulation_info", {})
        .get("instruments", [])
        if isinstance(payload.get("summary"), dict)
        else []
    )
    preferred_logs = [logs_dir / f"order_book_{instrument}.log" for instrument in instruments]
    preferred_logs.append(logs_dir / "order_book_AAPL.log")
    preferred_logs.extend(sorted(logs_dir.glob("order_book_*.log")))
    for path in preferred_logs:
        if path.exists():
            payload["order_log"] = path.read_text(encoding="utf-8", errors="replace")
            payload["order_log_path"] = str(path.relative_to(ROOT))
            break
    if not payload["order_log"]:
        payload["missing"].append(str((logs_dir / "order_book_<instrument>.log").relative_to(ROOT)))

    shock_log = logs_dir / "agents" / "agent_shock_agent.log"
    if shock_log.exists():
        payload["shock_log"] = shock_log.read_text(encoding="utf-8", errors="replace")

    return payload


def load_agent_reports(agent_reports_dir: Path) -> dict[str, dict[str, Any]]:
    if not agent_reports_dir.exists():
        return {}

    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(agent_reports_dir.glob("metrics_*.json")):
        agent_id = path.stem.removeprefix("metrics_")
        reports.setdefault(agent_id, {})["metrics"] = read_json_file(path)

    for path in sorted(agent_reports_dir.glob("portfolio_timeseries_*.json")):
        agent_id = path.stem.removeprefix("portfolio_timeseries_")
        series = read_json_file(path)
        if isinstance(series, list):
            reports.setdefault(agent_id, {})["portfolio_timeseries"] = series[-250:]
            reports[agent_id]["last_portfolio_value"] = (
                series[-1].get("value") if series and isinstance(series[-1], dict) else None
            )

    for path in sorted(agent_reports_dir.glob("pending_orders_*.json")):
        agent_id = path.stem.removeprefix("pending_orders_")
        pending = read_json_file(path)
        reports.setdefault(agent_id, {})["pending_order_count"] = (
            len(pending) if isinstance(pending, dict) else 0
        )

    for path in sorted(agent_reports_dir.glob("executed_orders_*.json")):
        agent_id = path.stem.removeprefix("executed_orders_")
        executed = read_json_file(path)
        reports.setdefault(agent_id, {})["executed_order_count"] = (
            len(executed) if isinstance(executed, list) else 0
        )

    return reports


def read_json_file(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def load_configured_agents(run_dir: Path) -> list[dict[str, Any]]:
    config_path = run_dir / "stocksim_config.yaml"
    if not config_path.exists():
        return []
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return []

    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        return []

    configured = []
    for agent_id, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        configured.append(
            {
                "id": str(agent_id),
                "type": str(spec.get("type", "Agent")),
                "count": int(spec.get("count", 1) or 1),
            }
        )
    return configured


def read_json_if_exists(path: Path, payload: dict[str, Any]) -> Any:
    if not path.exists():
        payload["missing"].append(str(path.relative_to(ROOT)))
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        payload["missing"].append(f"{path.relative_to(ROOT)} (not ready)")
        return None


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    server_version = "AMLDashboardServer/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header(
                "Location",
                f"/dashboard.html?run={DEFAULT_RUN_ID}",
            )
            self.end_headers()
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/api/status":
            self.write_json(STATE.snapshot())
            return
        if parsed.path == "/api/scenarios":
            scenarios = [
                str(path.relative_to(ROOT))
                for path in sorted((ROOT / "scenarios").glob("*.yaml"))
            ]
            self.write_json({"scenarios": scenarios})
            return
        if parsed.path == "/api/artifacts":
            params = parse_qs(parsed.query)
            try:
                payload = load_run_artifacts(
                    params.get("run_id", [DEFAULT_RUN_ID])[0],
                )
            except Exception as exc:
                self.write_json({"status": "error", "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.write_json(payload)
            return
        if parsed.path == "/api/live":
            params = parse_qs(parsed.query)
            try:
                run_id = validate_run_id(params.get("run_id", [DEFAULT_RUN_ID])[0])
            except Exception as exc:
                self.write_json({"status": "error", "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.stream_live_run(run_id)
            return
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self.read_json_body()
            job = STATE.start_job(
                scenario=str(payload.get("scenario") or DEFAULT_SCENARIO),
                run_id=str(payload.get("run_id") or DEFAULT_RUN_ID),
                reports=bool(payload.get("reports", True)),
            )
        except Exception as exc:
            self.write_json({"status": "error", "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self.write_json(job.to_dict(), HTTPStatus.ACCEPTED)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def write_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream_live_run(self, run_id: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"retry: 1000\n\n")
        self.wfile.flush()

        while True:
            try:
                payload = load_run_artifacts(run_id)
                payload["server_time"] = time.time()
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.wfile.write(b"event: snapshot\n")
                self.wfile.write(b"data: " + body + b"\n\n")
                self.wfile.flush()
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                error_body = json.dumps({"error": str(exc)}).encode("utf-8")
                try:
                    self.wfile.write(b"event: error\n")
                    self.wfile.write(b"data: " + error_body + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(1)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def start_rabbitmq() -> None:
    if rabbitmq_reachable():
        print("RabbitMQ already reachable on 127.0.0.1:5672.")
        return

    docker_cmd = shutil.which("docker")
    if docker_cmd is None:
        raise RuntimeError(
            "Docker was not found on PATH, and RabbitMQ is not reachable on "
            "127.0.0.1:5672. Install/start Docker Desktop, or start RabbitMQ "
            "manually, then rerun without --start-rabbitmq: "
            "python dashboard_server.py --run --run-id local_four_agent"
        )

    subprocess.run(
        [
            docker_cmd,
            "compose",
            "-f",
            str(DOCKER_COMPOSE_FILE),
            "up",
            "-d",
            "rabbitmq",
        ],
        cwd=ROOT,
        check=True,
    )
    wait_for_rabbitmq(docker_cmd=docker_cmd)


def wait_for_rabbitmq(
    docker_cmd: str | None = None,
    timeout_seconds: int = 60,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if rabbitmq_reachable():
            print("RabbitMQ is reachable on 127.0.0.1:5672.")
            return
        if docker_cmd is not None:
            result = subprocess.run(
                [
                    docker_cmd,
                    "inspect",
                    "-f",
                    "{{.State.Health.Status}}",
                    "stocksim-rabbitmq",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            status = result.stdout.strip()
            if status == "healthy":
                print("RabbitMQ container is healthy.")
                return
            if status == "unhealthy":
                raise RuntimeError("RabbitMQ container reported unhealthy status.")
        time.sleep(2)
    raise TimeoutError("Timed out waiting for RabbitMQ to become healthy.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the AML dashboard and optionally launch a simulation.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--run", action="store_true", help="Start the simulation immediately.")
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Do not pass --reports to aml_runner.py.",
    )
    parser.add_argument(
        "--start-rabbitmq",
        action="store_true",
        help="Start the StockSim RabbitMQ container before serving.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.start_rabbitmq:
        print("Starting RabbitMQ via Docker Compose...", flush=True)
        try:
            start_rabbitmq()
        except Exception as exc:
            print(f"Could not start RabbitMQ: {exc}", file=sys.stderr)
            return 1

    if args.run:
        if not args.start_rabbitmq and not rabbitmq_reachable():
            print(
                "Warning: RabbitMQ is not reachable on 127.0.0.1:5672. "
                "The simulation may fail unless RabbitMQ is already running "
                "somewhere reachable by the scenario.",
                file=sys.stderr,
            )
        job = STATE.start_job(
            scenario=args.scenario,
            run_id=args.run_id,
            reports=not args.no_reports,
        )
        print(f"Started simulation run: {job.run_id}")
        args.run_id = job.run_id

    server = ThreadingHTTPServer((args.host, args.port), DashboardRequestHandler)
    url = f"http://{args.host}:{args.port}/dashboard.html?run={args.run_id}"
    print(f"Serving AML dashboard at {url}", flush=True)
    print("Press Ctrl+C to stop the dashboard server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
