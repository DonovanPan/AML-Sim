"""Base AML trading agent with shared fast/slow loop orchestration."""

from __future__ import annotations

import inspect
import json
import os
from abc import abstractmethod
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from aml_sim.agents.context.memory import LocalAgentMemory, MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import AgentProfile
from aml_sim.agents.strategy.llm_slow_strategy import SlowStrategist
from aml_sim.agents.strategy.validator import StrategyValidationError, validate_strategy_state
from agents.benchmark_traders.trader import TraderAgent


class BaseAMLAgent(TraderAgent):
    """
    Shared AML agent loop on top of StockSim's TraderAgent.

    StockSim owns messaging, execution, accounting, portfolio state, and order
    state. AML owns profile, memory, observation packaging, strategy updates,
    validation, and role-specific fast execution policy.
    """

    def __init__(
        self,
        instrument_exchange_map: dict[str, str],
        strategy_state: Any,
        *,
        profile: Optional[AgentProfile | Mapping[str, Any]] = None,
        memory: Optional[MemoryBackend] = None,
        observation_processor: Optional[ObservationProcessor] = None,
        slow_strategist: Optional[SlowStrategist] = None,
        strategy_validator: Optional[Callable[[Any], Any]] = None,
        slow_loop_interval_seconds: Optional[int] = None,
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **trader_kwargs: Any,
    ) -> None:
        super().__init__(
            instrument_exchange_map=instrument_exchange_map,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )

        self.profile = profile or {}
        self.memory = memory or LocalAgentMemory()
        self.observation_processor = observation_processor or ObservationProcessor()
        if slow_strategist is None:
            raise ValueError("BaseAMLAgent requires a slow_strategist.")
        self.slow_strategist = slow_strategist
        self.strategy_validator = strategy_validator or validate_strategy_state
        self.strategy_state = self._validate_or_keep(strategy_state)

        self.slow_loop_interval = timedelta(
            seconds=slow_loop_interval_seconds or self.action_interval.total_seconds()
        )
        self.next_slow_loop_time = None
        self.action_events: list[dict[str, Any]] = []
        self.logger.info(
            f"{self.agent_id} slow loop configured: "
            f"strategist={type(self.slow_strategist).__name__}, "
            f"interval={self.slow_loop_interval}"
        )

    async def handle_time_tick(self, payload: dict[str, Any]) -> None:
        await super().handle_time_tick(payload)

        current_time = self.current_time
        if current_time is None:
            return

        if self.next_action_time is None:
            self.next_action_time = current_time
        if self.next_slow_loop_time is None:
            self.next_slow_loop_time = current_time

        observation = self.build_observation()

        if self.slow_loop_due():
            await self.run_slow_loop(observation)
            self.next_slow_loop_time = current_time + self.slow_loop_interval
            observation = self.build_observation()

        if current_time >= self.next_action_time:
            await self.run_fast_loop(observation)
            self.next_action_time = current_time + self.action_interval

    def build_observation(self) -> dict[str, Any]:
        fresh_observation = self.observation_processor.build_context(
            self,
            profile=self.profile,
        )
        memory_context = self.memory.retrieve_context(
            self.agent_id,
            observation=fresh_observation,
        )
        return self.observation_processor.build_context(
            self,
            profile=self.profile,
            memory=memory_context,
        )

    def slow_loop_due(self) -> bool:
        return (
            self.current_time is not None
            and self.next_slow_loop_time is not None
            and self.current_time >= self.next_slow_loop_time
        )

    async def run_slow_loop(self, observation: Mapping[str, Any]) -> None:
        before = self._strategy_snapshot(self.strategy_state)
        proposal = self.slow_strategist.propose(
            observation,
            self.strategy_state,
            profile=self.profile,
            memory=observation.get("memory", {}),
        )
        if inspect.isawaitable(proposal):
            proposal = await proposal

        self.strategy_state = self._validate_or_keep(proposal)
        after = self._strategy_snapshot(self.strategy_state)
        self.logger.info(
            f"{self.agent_id} slow loop completed: "
            f"strategist={type(self.slow_strategist).__name__}, "
            f"before={before}, after={after}"
        )

    def _validate_or_keep(self, proposal: Any) -> Any:
        try:
            validator = self.strategy_validator
            if hasattr(validator, "validate"):
                return validator.validate(proposal)
            return validator(proposal)
        except StrategyValidationError as exc:
            self.logger.warning(
                f"Rejected strategy proposal for {self.agent_id}; keeping previous state: {exc}"
            )
            return getattr(self, "strategy_state", proposal)

    def _strategy_snapshot(self, strategy_state: Any) -> dict[str, Any]:
        if is_dataclass(strategy_state):
            return asdict(strategy_state)
        if isinstance(strategy_state, Mapping):
            return dict(strategy_state)
        return dict(vars(strategy_state))

    async def place_order(
        self,
        instrument: str,
        side: str,
        quantity: int,
        order_type: str,
        price: Optional[float] = None,
        oco_group: Optional[str] = None,
        explanation: Optional[str] = None,
        is_short: bool = False,
        is_short_cover: bool = False,
    ) -> Optional[str]:
        before = self._portfolio_snapshot()
        order_id = await super().place_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            oco_group=oco_group,
            explanation=explanation,
            is_short=is_short,
            is_short_cover=is_short_cover,
        )
        self._record_action_event(
            {
                "event_type": "order_submitted" if order_id else "order_rejected",
                "order_id": order_id,
                "instrument": instrument,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "price": price,
                "explanation": explanation,
                "strategy_state": self._strategy_snapshot(self.strategy_state),
                "portfolio_before": before,
                "portfolio_after": self._portfolio_snapshot(),
            }
        )
        return order_id

    async def on_trade_execution(self, trade_data: dict[str, Any]) -> None:
        before = self._portfolio_snapshot()
        await super().on_trade_execution(trade_data)
        self._record_action_event(
            {
                "event_type": "trade_executed",
                "order_id": trade_data.get("order_id"),
                "instrument": trade_data.get("instrument"),
                "role": trade_data.get("role"),
                "quantity": trade_data.get("quantity"),
                "price": trade_data.get("price"),
                "order_type": trade_data.get("order_type"),
                "order_status": trade_data.get("order_status"),
                "explanation": trade_data.get("explanation"),
                "raw_trade": dict(trade_data),
                "strategy_state": self._strategy_snapshot(self.strategy_state),
                "portfolio_before": before,
                "portfolio_after": self._portfolio_snapshot(),
            }
        )

    def _record_action_event(self, event: dict[str, Any]) -> None:
        event.setdefault("agent_id", self.agent_id)
        event.setdefault("timestamp", self.current_time.isoformat() if self.current_time else None)
        self.action_events.append(self._serialize_value(event))

    def _portfolio_snapshot(self) -> dict[str, Any]:
        instruments = list(self.instrument_exchange_map.keys())
        return {
            "cash": self.cash,
            "portfolio_value": self.portfolio_value,
            "positions": {
                instrument: {
                    "long": self.long_qty[instrument],
                    "short": self.short_qty[instrument],
                    "net": self.long_qty[instrument] - self.short_qty[instrument],
                    "last_price": self.prices[instrument],
                    "realized_pnl": self.realized_pnl[instrument],
                }
                for instrument in instruments
            },
        }

    def stop(self) -> None:
        self._export_action_events()
        super().stop()

    def _export_action_events(self) -> None:
        output_dir = os.getenv("METRICS_OUTPUT_DIR", "metrics")
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"trader_actions_{self.agent_id}.json")
        try:
            with open(output_file, "w", encoding="utf-8") as handle:
                json.dump(self.action_events, handle, indent=2)
            self.logger.info(f"AML trader actions exported to {output_file}")
        except Exception as exc:
            self.logger.error(f"Failed to export AML trader actions: {exc}")

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if is_dataclass(value):
            return self._serialize_value(asdict(value))
        if isinstance(value, Mapping):
            return {str(key): self._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._serialize_value(item) for item in value]
        return value

    @abstractmethod
    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        """Execute role-specific trading behavior from the current strategy."""
