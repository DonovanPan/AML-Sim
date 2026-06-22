"""Base class for all AML-Sim trading agents."""
from __future__ import annotations

from typing import Any, Dict, Optional

from agents.benchmark_traders.trader import TraderAgent

# Parameter names that AML agents forward to StockSim's TraderAgent.__init__
_TRADER_AGENT_PARAMS = (
    "initial_cash",
    "initial_positions",
    "initial_cost_basis",
    "action_interval_seconds",
)


class AMLTraderAgent(TraderAgent):
    """
    Common base for AML-specific trading agents.

    Handles extraction of StockSim TraderAgent parameters from kwargs so each
    concrete agent does not repeat the same pattern.  Subclasses should accept
    **kwargs in __init__ and call super().__init__(...) with their own
    specific parameters plus **kwargs.
    """

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **kwargs,
    ) -> None:
        trader_kwargs = {
            param: kwargs[param]
            for param in _TRADER_AGENT_PARAMS
            if param in kwargs
        }
        super().__init__(
            instrument_exchange_map=instrument_exchange_map,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )
