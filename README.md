# AML-Sim

AML-Sim is an experiment layer on top of the `StockSim` market simulator. The
goal is to study market decision making, robustness, and behavioral finance in
synthetic multi-agent markets while keeping scenario orchestration outside the
StockSim submodule.

`simulators/StockSim` is a git submodule. Changes inside StockSim are committed
and pushed from that directory, then the parent AML-Sim repository commits the
updated submodule pointer.

## Built On StockSim

AML-Sim builds on top of
[StockSim](https://github.com/shmaiii/StockSim), which is included as a git
submodule under `simulators/StockSim`. StockSim provides the core market
simulation engine, exchange agents, trader framework, RabbitMQ-based
coordination, and YAML-driven simulation launcher.

This repository adds AML-specific scenario orchestration and synthetic market
participants for studying market behavior, robustness, and decision making.

## Repository Layout

```text
AML-Sim/
├── aml_runner.py                       # AML scenario runner
├── scenarios/
│   └── aml_orderbook_replay.yaml       # Current AML smoke scenario
└── simulators/
    └── StockSim/                       # StockSim submodule
        ├── main_launcher.py            # StockSim entrypoint
        ├── docker-compose.yml          # RabbitMQ + StockSim services
        └── agents/aml/                 # AML-specific trader agents
```

## Architecture

AML-Sim currently has a thin orchestration layer around StockSim:

1. `aml_runner.py` reads an AML scenario YAML file.
2. The scenario's `stocksim_config` section is extracted and written to
   `.aml_runs/<run-id>/stocksim_config.yaml`.
3. `aml_runner.py` launches `simulators/StockSim/main_launcher.py` with that
   generated StockSim config.
4. StockSim starts the exchange agents, trader agents, and simulation clock.
5. Components communicate through RabbitMQ.
6. Logs for AML-launched runs are written under `.aml_runs/<run-id>/logs`.

The scenario YAML is the experiment definition. It contains AML-level metadata
such as `name`, `description`, and `rabbitmq_host`, plus the `stocksim_config`
mapping that is passed directly into StockSim after generation. In other words,
the YAML file is where you configure instruments, exchange mode, agents,
simulation times, and environment settings for a StockSim run.

## What AML Has Right Now

The current AML layer is an order book smoke-test setup using synthetic market
participants:

- `AML_Market_Maker`: posts bid/ask limit orders around a configurable fair
  price and adjusts quotes with an inventory skew.
- `AML_Retail_Trader`: submits occasional small noisy market orders with a
  configurable buy bias and trade probability.
- `AML_Institutional_Trader`: works toward target positions using sliced child
  orders.
- `aml_orderbook_replay.yaml`: runs a short synthetic AAPL order book scenario
  with one market maker, five retail traders, and one institutional trader.

These AML agents live in `simulators/StockSim/agents/aml/` and are registered in
StockSim's `AGENT_TYPE_MAPPING` inside `simulators/StockSim/main_launcher.py`.

## Setup

Clone the repo with submodules in one step:

```bash
git clone --recurse-submodules <AML-Sim repo URL>
cd AML-Sim
```

Or clone normally, then initialize the StockSim submodule afterward:

```bash
git clone <AML-Sim repo URL>
cd AML-Sim
git submodule update --init --recursive
```

Create and activate a Python environment from the AML-Sim root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r simulators/StockSim/requirements.txt
```

For the current synthetic order book scenario, no Polygon, Alpha Vantage, or LLM
API key is required. RabbitMQ is required.

Optional root `.env`:

```bash
RABBITMQ_HOST=localhost
LOG_DIR=logs
```

`aml_runner.py` will also set `LOG_DIR` to the run-specific log directory and
will pass `rabbitmq_host` from the scenario to StockSim.

## Start RabbitMQ

The easiest route is to use the StockSim Docker Compose file and start only
RabbitMQ:

```bash
cd simulators/StockSim
docker compose up -d rabbitmq
cd ../..
```

Before running a scenario, make sure the RabbitMQ container is actually up:

```bash
docker ps | grep rabbitmq
```

This should print the running RabbitMQ container, usually named
`stocksim-rabbitmq`. If it prints nothing, RabbitMQ is not running and StockSim
agents will fail to connect to the message broker, usually with a connection
refused or AMQP connection error.

## Run The Current AML Scenario

From the AML-Sim root, first check that the scenario can generate a valid
StockSim config:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml --dry-run
```

This creates a run directory under `.aml_runs/` and writes:

```text
.aml_runs/<run-id>/stocksim_config.yaml
```

Then run the full scenario with RabbitMQ running:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml
```

You can set a stable run directory name while iterating:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml --run-id smoke_orderbook
```

Use a new `--run-id` each time, because the runner intentionally refuses to
overwrite an existing `.aml_runs/<run-id>` directory.

## Working With The StockSim Submodule

When editing files under `simulators/StockSim`, commit and push those changes
from inside the submodule:

```bash
cd simulators/StockSim
git status
git add .
git commit -m "Update AML StockSim agents"
git push
```

Then commit the updated submodule pointer from the parent repo:

```bash
cd ../..
git status
git add simulators/StockSim
git commit -m "Update StockSim submodule"
git push
```

Push the StockSim commit first. The parent repo only stores a pointer to a
specific StockSim commit, so other users need that commit to exist on the
StockSim remote.
