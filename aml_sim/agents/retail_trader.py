"""
AML Retail Trader.

Models a small, noisy participant that trades occasionally with small market
orders. Later this agent can react to synthetic news and herding signals.
"""

from __future__ import annotations

import random
from typing import Any, Dict, Mapping, Optional

from aml_sim.agents.base import BaseAMLAgent
from aml_sim.agents.context.memory import MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import RetailProfile, coerce_profile
from aml_sim.agents.strategy.llm_slow_strategy import (
    SlowStrategist,
    create_static_retail_llm_strategist,
)
from aml_sim.agents.models.state import RetailStrategyState
from aml_sim.agents.strategy.signals import clamp, event_pressure, momentum_signal, price_series
from utils.orders import OrderType, Side


class AMLRetailTrader(BaseAMLAgent):
    """
    Basic retail-style participant for synthetic AML markets.

    Retail here means many small orders, noisy decisions, limited capital, and
    occasional overreaction. This first version is intentionally simple.
    """

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        trade_probability: float = 0.3,
        max_order_size: int = 25,
        buy_bias: float = 0.5,
        herding_tendency: float = 0.0,
        panic_level: float = 0.0,
        sentiment_sensitivity: float = 0.4,
        shock_sensitivity: float = 0.4,
        random_seed: Optional[int] = None,
        profile: Optional[RetailProfile | Mapping[str, Any]] = None,
        memory: Optional[MemoryBackend] = None,
        observation_processor: Optional[ObservationProcessor] = None,
        slow_loop_interval_seconds: Optional[int] = None,
        slow_strategist: Optional[SlowStrategist] = None,
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **kwargs,
    ) -> None:
        trader_kwargs = {}
        for param in [
            "initial_cash",
            "initial_positions",
            "initial_cost_basis",
            "action_interval_seconds",
        ]:
            if param in kwargs:
                trader_kwargs[param] = kwargs[param]

        super().__init__(
            instrument_exchange_map=instrument_exchange_map,
            strategy_state=RetailStrategyState(
                trade_probability=trade_probability,
                max_order_size=max(1, max_order_size),
                buy_bias=buy_bias,
                herding_tendency=herding_tendency,
                panic_level=panic_level,
                sentiment_sensitivity=sentiment_sensitivity,
                shock_sensitivity=shock_sensitivity,
            ),
            profile=coerce_profile(profile, RetailProfile),
            memory=memory,
            observation_processor=observation_processor,
            slow_strategist=slow_strategist or create_static_retail_llm_strategist(),
            slow_loop_interval_seconds=slow_loop_interval_seconds,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )
        self.random = random.Random(random_seed)

        self.logger.info(
            f"AMLRetailTrader {self.agent_id} initialized: "
            f"strategy_state={self.strategy_state}"
        )

    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        for instrument in self.instrument_exchange_map.keys():
            await self._maybe_trade(instrument)

    async def _maybe_trade(self, instrument: str) -> None:
        trade_probability, buy_bias = self._effective_retail_params(instrument)
        if self.random.random() > trade_probability:
            return

        quantity = self.random.randint(1, self._effective_max_order_size(instrument))
        side = Side.BUY.value if self.random.random() < buy_bias else Side.SELL.value

        if side == Side.SELL.value:
            held = self.long_qty[instrument]
            if held <= 0:
                self.logger.debug(f"Retail trader skipped SELL for {instrument}: no inventory")
                return
            quantity = min(quantity, held)

        order_id = await self.place_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET.value,
            explanation="AML retail noisy market order",
        )
        if order_id:
            self.logger.info(
                f"AMLRetailTrader {self.agent_id} placed {side} market order "
                f"for {quantity} {instrument}"
            )

    def _effective_retail_params(self, instrument: str) -> tuple[float, float]:
        strategy = self.strategy_state
        pressure = event_pressure(self._active_events(), instrument)
        prices = price_series(self.price_history, instrument, self.prices.get(instrument, 0))
        momentum = momentum_signal(prices, lookback_ticks=5)

        trade_probability = strategy.trade_probability
        trade_probability *= pressure["order_arrival_multiplier"]
        trade_probability += pressure["severity"] * strategy.shock_sensitivity * 0.35
        trade_probability += min(abs(momentum) * strategy.herding_tendency * 10, 0.2)

        buy_bias = strategy.buy_bias
        buy_bias += pressure["directional_bias"] * strategy.sentiment_sensitivity * 0.25
        if prices and prices[-1] > 0:
            buy_bias += clamp(
                (pressure["fundamental_price_shift"] / prices[-1])
                * strategy.sentiment_sensitivity
                * 5,
                -0.15,
                0.15,
            )
        buy_bias += clamp(momentum * strategy.herding_tendency * 5, -0.2, 0.2)
        if pressure["directional_bias"] < 0:
            buy_bias -= pressure["severity"] * strategy.panic_level * 0.2

        return clamp(trade_probability, 0.0, 1.0), clamp(buy_bias, 0.0, 1.0)

    def _effective_max_order_size(self, instrument: str) -> int:
        pressure = event_pressure(self._active_events(), instrument)
        size = self.strategy_state.max_order_size * pressure["risk_limit_multiplier"]
        return max(1, int(size))
