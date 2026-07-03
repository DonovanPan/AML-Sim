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
├── aml_sim/
│   ├── launcher.py                     # AML-owned StockSim component launcher
│   ├── reporting.py                    # AML-owned report orchestration
│   ├── runs.py                         # AML run directory/artifact helpers
│   ├── scenario.py                     # AML scenario loading/validation
│   └── agents/                         # AML-specific trader agents
│       ├── base.py                     # Shared fast/slow-loop AML agent base
│       ├── market_maker_trader.py      # AML market-maker agent
│       ├── retail_trader.py            # AML retail trader agent
│       ├── institutional_trader.py     # AML institutional trader agent
│       ├── models/
│       │   ├── profile.py              # Stable role/personality profile models
│       │   └── state.py                # Role-specific strategy state models
│       ├── context/
│       │   ├── observation.py          # LLM/slow-loop observation context
│       │   └── memory.py               # Local memory + future Zep hook
│       └── strategy/
│           ├── llm_slow_strategy.py    # LLM-shaped slow-loop strategist
│           └── validator.py            # Strategy state bounds validation
├── scenarios/
│   └── aml_orderbook_replay.yaml       # Current AML smoke scenario
└── simulators/
    └── StockSim/                       # StockSim submodule
        ├── main_launcher.py            # StockSim entrypoint
        └── docker-compose.yml          # RabbitMQ + StockSim services
```

## Architecture

AML-Sim is split into two layers:

- Run orchestration: AML-Sim reads scenarios, creates run artifacts, and starts
  StockSim engine components.
- Agent behavior: AML-Sim owns the market-maker, retail, and institutional
  trader behavior while keeping those agents compatible with StockSim's
  `TraderAgent`.

### Run Orchestration

1. `aml_runner.py` reads an AML scenario YAML file.
2. The scenario's `stocksim_config` section is extracted and written to
   `.aml_runs/<run-id>/stocksim_config.yaml`.
3. AML-Sim also archives the original scenario as
   `.aml_runs/<run-id>/scenario.yaml` and writes run metadata to
   `.aml_runs/<run-id>/metadata.json`.
4. AML-Sim imports StockSim exchange/base-trader/simulation-clock classes and
   starts those component processes itself. The AML agent behavior classes live
   under `aml_sim/agents/`. `simulators/StockSim/main_launcher.py` remains
   StockSim's standalone CLI entrypoint.
5. AML-Sim starts the exchange agents, trader agents, and simulation clock.
6. Components communicate through RabbitMQ.
7. Logs for AML-launched runs are written under `.aml_runs/<run-id>/logs`.

The scenario YAML is the experiment definition. It contains AML-level metadata
such as `name`, `description`, and `rabbitmq_host`, plus the `stocksim_config`
mapping that is passed directly into StockSim after generation. In other words,
the YAML file is where you configure instruments, exchange mode, agents,
simulation times, and environment settings for a StockSim run.

### Agent Layer

The AML agent layer currently includes these synthetic market participants:

- `AML_Market_Maker`: posts bid/ask limit orders around a configurable fair
  price and adjusts quotes with an inventory skew.
- `AML_Retail_Trader`: submits occasional small noisy market orders with a
  configurable buy bias and trade probability.
- `AML_Institutional_Trader`: works toward target positions using sliced child
  orders.
- `aml_orderbook_replay.yaml`: runs a short synthetic AAPL order book scenario
  with one market maker, five retail traders, and one institutional trader.

These AML agents live in `aml_sim/agents/`. They still inherit StockSim's
`TraderAgent` and use StockSim's order/message primitives, but AML-Sim owns
their behavior and maps YAML types such as `AML_Market_Maker` to these classes.

AML agents now use a shared fast-loop / slow-loop architecture:

- `BaseAMLAgent` inherits from StockSim's `TraderAgent` and keeps the shared AML
  agent plumbing in one place.
- StockSim still owns execution, messaging, portfolio/accounting state, order
  state, and RabbitMQ integration.
- AML-Sim owns behavioral strategy state, observation packaging, memory hooks,
  strategy validation, slow-loop strategy updates, and role-specific fast
  execution behavior.
- `action_interval` controls how often the fast loop is allowed to submit
  orders.
- `slow_loop_interval` controls how often the slow loop updates the agent's
  strategy state.

The fast loop is role-specific and runs from the currently validated strategy
state:

- Market maker fast loop refreshes bid/ask quotes using fair price, spread,
  quote size, target inventory, and inventory skew.
- Retail fast loop submits small probabilistic market orders using trade
  probability, buy bias, and max order size.
- Institutional fast loop works toward target positions using child order size,
  order type, and execution style.

The slow loop currently uses a fixed JSON LLM test path through
`aml_sim/agents/strategy/llm_slow_strategy.py`. This proves the LLM-shaped
control flow before wiring a real LLM API client. The fixed responses are
role-specific, so the market maker, retail trader, and institutional trader each
receive different strategy updates.

### Strategy State And Validation

Role-specific strategy states live in `aml_sim/agents/models/state.py`:

- `MarketMakerStrategyState`
- `RetailStrategyState`
- `InstitutionalStrategyState`

Before a strategy proposal is applied, `aml_sim/agents/strategy/validator.py`
checks bounds such as trade probability, buy bias, quote size, spread, child
order size, confidence, and risk mode. If validation fails, the agent keeps its
previous strategy state and logs the rejection.

### Observation Context For LLM Strategy

The observation processor in `aml_sim/agents/context/observation.py` builds the
structured context package used by the slow loop. Today that package includes:

- agent id
- current simulation time
- latest market snapshot
- cash, portfolio value, and per-instrument inventory
- pending orders
- recent fills
- current strategy state
- memory context
- future shock/event context

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
pip install -r requirements.txt
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
.aml_runs/<run-id>/scenario.yaml
.aml_runs/<run-id>/stocksim_config.yaml
.aml_runs/<run-id>/metadata.json
.aml_runs/<run-id>/logs/
.aml_runs/<run-id>/charts/
.aml_runs/<run-id>/reports/
```

Completed AML runs also write trader action artifacts under:

```text
.aml_runs/<run-id>/reports/agents/
.aml_runs/<run-id>/reports/trader_actions.json
```

The combined `trader_actions.json` report contains submitted orders, rejected
orders, trade executions, strategy state at the time of the action, and
portfolio/share state before and after the action.

Then run the full scenario with RabbitMQ running:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml
```

To call StockSim's post-simulation artifact generator and save reports/charts
inside the AML run directory, add `--reports`:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml --reports
```

For the current synthetic order book scenario, this writes the StockSim summary
JSON under `.aml_runs/<run-id>/reports/`. Future AML-specific reports should use
the same run-local `reports/` and `charts/` folders, but can add synthetic
orderbook/trade HTML views instead of relying only on external candle data.

You can set a stable run directory name while iterating:

```bash
python aml_runner.py scenarios/aml_orderbook_replay.yaml --run-id smoke_orderbook
```

Use a new `--run-id` each time, because the runner intentionally refuses to
overwrite an existing `.aml_runs/<run-id>` directory.

## Run From The Dashboard

For local iteration, the dashboard can serve the UI and launch a simulation from
one small Python server. If Docker Desktop is installed and the `docker`
command works in your terminal, use:

```bash
python dashboard_server.py --start-rabbitmq --run --run-id one_hour_live
```

If Docker is not installed or is not on your `PATH`, start RabbitMQ manually on
`localhost:5672` first, then run the dashboard without `--start-rabbitmq`:

```bash
python dashboard_server.py --run --run-id one_hour_live
```

Open the printed URL, or go directly to:

```text
http://127.0.0.1:8765/dashboard.html?run=one_hour_live
```

The `Run Simulation` button in `dashboard.html` works only when the page is
served by `dashboard_server.py`, because plain `python3 -m http.server 8765`
cannot start local Python processes. If RabbitMQ is already running and you only
want the UI/API server, use:

```bash
python dashboard_server.py
```

While a simulation is running, the dashboard streams run artifacts from
`/api/live` and updates the price chart, order book, trade tape, shock monitor,
participant activity, and top-line stats as the StockSim logs are written. Final
report files are still loaded after shutdown for completed-run metrics.

Runs are finite by default. The scenario YAML controls the simulated clock with
`simulation.start_time`, `simulation.end_time`, and `simulation.tick_interval`.
For example, `scenarios/aml_one_hour_live.yaml` runs from 09:30 to 10:30 with
30-second ticks. The dashboard streams updates while this run is active; after
the scenario clock reaches `end_time`, the run stops and the final reports are
loaded. StockSim currently sleeps for roughly 5 wall-clock seconds per tick, so
this one simulated hour usually takes about 10 wall-clock minutes plus
startup/reporting overhead.

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
