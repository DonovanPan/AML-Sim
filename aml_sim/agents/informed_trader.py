"""AML informed trader participant."""

from __future__ import annotations

import random
from typing import Any, Dict, Mapping, Optional

from aml_sim.agents.base import BaseAMLAgent
from aml_sim.agents.context.memory import MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import InformedProfile, coerce_profile
from aml_sim.agents.models.state import InformedStrategyState
from aml_sim.agents.strategy.llm_slow_strategy import (
    SlowStrategist,
    create_static_informed_llm_strategist,
)
from aml_sim.agents.strategy.signals import (
    clamp,
    event_pressure,
    momentum_signal,
    price_series,
)
from utils.orders import OrderType, Side


class AMLInformedTrader(BaseAMLAgent):
    """
    Participant that trades from a private/fundamental value estimate.

    This is intentionally not a separate momentum or mean-reversion fund. It is
    a role-level market participant with a value edge, and it may use recent
    momentum as a minor execution timing input.
    """

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        fair_value_anchor: float = 100.0,
        information_edge: float = 0.7,
        trade_probability: float = 0.35,
        max_order_size: int = 50,
        max_position: int = 750,
        min_position: int = 0,
        signal_threshold: float = 0.002,
        momentum_weight: float = 0.2,
        fundamental_sensitivity: float = 1.0,
        shock_reactivity: float = 0.8,
        order_type: str = OrderType.MARKET.value,
        limit_offset: float = 0.03,
        random_seed: Optional[int] = None,
        profile: Optional[InformedProfile | Mapping[str, Any]] = None,
        memory: Optional[MemoryBackend] = None,
        observation_processor: Optional[ObservationProcessor] = None,
        slow_loop_interval_seconds: Optional[int] = None,
        slow_strategist: Optional[SlowStrategist] = None,
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **kwargs: Any,
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
            strategy_state=InformedStrategyState(
                fair_value_anchor=fair_value_anchor,
                information_edge=information_edge,
                trade_probability=trade_probability,
                max_order_size=max(1, max_order_size),
                max_position=max_position,
                min_position=min_position,
                signal_threshold=signal_threshold,
                momentum_weight=momentum_weight,
                fundamental_sensitivity=fundamental_sensitivity,
                shock_reactivity=shock_reactivity,
                order_type=order_type.upper(),
                limit_offset=limit_offset,
            ),
            profile=coerce_profile(profile, InformedProfile),
            memory=memory,
            observation_processor=observation_processor,
            slow_strategist=slow_strategist or create_static_informed_llm_strategist(),
            slow_loop_interval_seconds=slow_loop_interval_seconds,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )
        self.random = random.Random(random_seed)
        self.logger.info(
            f"AMLInformedTrader {self.agent_id} initialized: "
            f"strategy_state={self.strategy_state}"
        )

    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        for instrument in self.instrument_exchange_map.keys():
            await self._trade_signal(instrument)

    async def _trade_signal(self, instrument: str) -> None:
        strategy = self.strategy_state
        current_price = self._current_price(instrument)
        if current_price <= 0:
            return

        pressure = event_pressure(self._active_events(), instrument)
        fair_value = max(
            0.01,
            strategy.fair_value_anchor
            + pressure["fundamental_price_shift"] * strategy.fundamental_sensitivity,
        )
        prices = price_series(self.price_history, instrument, current_price)
        value_signal = (fair_value / current_price) - 1.0
        timing_signal = momentum_signal(prices, lookback_ticks=8) * strategy.momentum_weight
        shock_signal = (
            pressure["directional_bias"]
            * strategy.shock_reactivity
            * strategy.signal_threshold
        )
        signal = value_signal + timing_signal + shock_signal
        strategy.signal_strength = signal

        if abs(signal) < strategy.signal_threshold:
            return

        participation = strategy.trade_probability
        participation *= pressure["order_arrival_multiplier"]
        participation *= 0.5 + (strategy.information_edge * 0.5)
        if self.random.random() > clamp(participation, 0.0, 1.0):
            return

        side = Side.BUY.value if signal > 0 else Side.SELL.value
        quantity = self._order_quantity(instrument, side, pressure)
        if quantity <= 0:
            return

        price = None
        if strategy.order_type == OrderType.LIMIT.value:
            price = self._aggressive_limit_price(current_price, side)

        order_id = await self.place_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=strategy.order_type,
            price=price,
            explanation=(
                f"AML informed value signal {signal:.5f}; "
                f"fair_value={fair_value:.2f}, price={current_price:.2f}"
            ),
        )
        if order_id:
            self.logger.info(
                f"AMLInformedTrader {self.agent_id} placed {side} "
                f"{strategy.order_type} order for {quantity} {instrument} "
                f"(signal={signal:.5f})"
            )

    def _current_price(self, instrument: str) -> float:
        prices = price_series(
            self.price_history,
            instrument,
            self.prices.get(instrument, self.strategy_state.fair_value_anchor),
        )
        return prices[-1] if prices else self.strategy_state.fair_value_anchor

    def _order_quantity(
        self,
        instrument: str,
        side: str,
        pressure: Mapping[str, float],
    ) -> int:
        strategy = self.strategy_state
        current_position = self.long_qty[instrument] - self.short_qty[instrument]
        max_position = max(
            strategy.min_position,
            int(strategy.max_position * pressure["risk_limit_multiplier"]),
        )
        max_size = max(1, int(strategy.max_order_size * pressure["risk_limit_multiplier"]))
        quantity = self.random.randint(1, max_size)

        if side == Side.BUY.value:
            return min(quantity, max(0, max_position - current_position))

        held = self.long_qty[instrument]
        return min(quantity, held)

    def _aggressive_limit_price(self, current_price: float, side: str) -> float:
        offset = self.strategy_state.limit_offset
        if side == Side.BUY.value:
            return round(current_price + offset, 2)
        return round(max(0.01, current_price - offset), 2)
