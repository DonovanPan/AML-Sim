"""Observation context builder for AML agents."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Optional

from aml_sim.agents.models.profile import profile_to_dict
from aml_sim.serialization import serialize_mapping, serialize_value


DEFAULT_RECENT_FILL_LIMIT = 10


def build_observation_context(
    agent: Any,
    *,
    profile: Optional[Mapping[str, Any]] = None,
    memory: Optional[Mapping[str, Any]] = None,
    events: Optional[list[Mapping[str, Any]]] = None,
    recent_fill_limit: int = DEFAULT_RECENT_FILL_LIMIT,
) -> dict[str, Any]:
    """
    Build a clean context dictionary from a StockSim-compatible trading agent.

    This is the input package the future slow LLM loop will read before updating
    strategy state. The function is intentionally tolerant of missing fields so
    it can work with simple agents now and richer AML agents later.
    """

    instruments = list(getattr(agent, "instrument_exchange_map", {}).keys())

    return {
        "agent": _build_agent_context(agent, profile),
        "current_time": serialize_value(getattr(agent, "current_time", None)),
        "market": {
            "last_market_snapshot": serialize_mapping(
                getattr(agent, "last_market_snapshot", {})
            ),
            "price_history": serialize_mapping(getattr(agent, "price_history", {})),
        },
        "portfolio": _build_portfolio_context(agent, instruments),
        "orders": {
            "pending": serialize_mapping(getattr(agent, "pending_orders", {})),
            "count": len(getattr(agent, "pending_orders", {}) or {}),
        },
        "recent_fills": _recent_fills(agent, recent_fill_limit),
        "strategy_state": _strategy_state(agent),
        "memory": serialize_mapping(memory or {}),
        "events": [serialize_mapping(event) for event in (events or [])],
        "event_count": len(events or []),
    }


class ObservationProcessor:
    """Small wrapper object for agents that prefer an instance dependency."""

    def __init__(self, recent_fill_limit: int = DEFAULT_RECENT_FILL_LIMIT) -> None:
        self.recent_fill_limit = recent_fill_limit

    def build_context(
        self,
        agent: Any,
        *,
        profile: Optional[Mapping[str, Any]] = None,
        memory: Optional[Mapping[str, Any]] = None,
        events: Optional[list[Mapping[str, Any]]] = None,
    ) -> dict[str, Any]:
        return build_observation_context(
            agent,
            profile=profile,
            memory=memory,
            events=events,
            recent_fill_limit=self.recent_fill_limit,
        )


def _build_agent_context(
    agent: Any,
    profile: Optional[Any],
) -> dict[str, Any]:
    return {
        "agent_id": getattr(agent, "agent_id", None),
        "profile": serialize_mapping(profile_to_dict(profile)),
    }


def _build_portfolio_context(agent: Any, instruments: list[str]) -> dict[str, Any]:
    long_qty = getattr(agent, "long_qty", {})
    short_qty = getattr(agent, "short_qty", {})
    prices = getattr(agent, "prices", {})
    realized_pnl = getattr(agent, "realized_pnl", {})

    return {
        "cash": getattr(agent, "cash", None),
        "portfolio_value": getattr(agent, "portfolio_value", None),
        "inventory": {
            instrument: {
                "long": long_qty.get(instrument, 0),
                "short": short_qty.get(instrument, 0),
                "net": long_qty.get(instrument, 0) - short_qty.get(instrument, 0),
                "last_price": prices.get(instrument, 0),
                "realized_pnl": realized_pnl.get(instrument, 0),
            }
            for instrument in instruments
        },
    }


def _recent_fills(agent: Any, recent_fill_limit: int) -> list[Any]:
    fills = getattr(agent, "session_executed_orders", []) or []
    if recent_fill_limit <= 0:
        return []
    return [serialize_value(fill) for fill in fills[-recent_fill_limit:]]


def _strategy_state(agent: Any) -> dict[str, Any]:
    strategy_state = getattr(agent, "strategy_state", None)
    if strategy_state is None:
        return {}
    if is_dataclass(strategy_state):
        return serialize_mapping(asdict(strategy_state))
    if isinstance(strategy_state, Mapping):
        return serialize_mapping(strategy_state)
    return serialize_mapping(vars(strategy_state))
