"""AML liquidity taker participant."""

from __future__ import annotations

import random
from typing import Any, Dict, Mapping, Optional

from aml_sim.agents.base import BaseAMLAgent
from aml_sim.agents.context.memory import MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import LiquidityTakerProfile, coerce_profile
from aml_sim.agents.models.state import LiquidityTakerStrategyState
from aml_sim.agents.strategy.llm_slow_strategy import (
    SlowStrategist,
    create_static_liquidity_taker_llm_strategist,
)
from aml_sim.agents.strategy.signals import clamp, event_pressure
from utils.orders import OrderType, Side


class AMLLiquidityTaker(BaseAMLAgent):
    """Aggressive flow participant that consumes available displayed liquidity."""

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        flow_intensity: float = 0.35,
        buy_bias: float = 0.5,
        max_order_size: int = 40,
        inventory_limit: int = 500,
        shock_sensitivity: float = 0.7,
        aggression: float = 0.75,
        random_seed: Optional[int] = None,
        profile: Optional[LiquidityTakerProfile | Mapping[str, Any]] = None,
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
            strategy_state=LiquidityTakerStrategyState(
                flow_intensity=flow_intensity,
                buy_bias=buy_bias,
                max_order_size=max(1, max_order_size),
                inventory_limit=inventory_limit,
                shock_sensitivity=shock_sensitivity,
                aggression=aggression,
            ),
            profile=coerce_profile(profile, LiquidityTakerProfile),
            memory=memory,
            observation_processor=observation_processor,
            slow_strategist=slow_strategist or create_static_liquidity_taker_llm_strategist(),
            slow_loop_interval_seconds=slow_loop_interval_seconds,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )
        self.random = random.Random(random_seed)
        self.logger.info(
            f"AMLLiquidityTaker {self.agent_id} initialized: "
            f"strategy_state={self.strategy_state}"
        )

    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        for instrument in self.instrument_exchange_map.keys():
            await self._maybe_take_liquidity(instrument)

    async def _maybe_take_liquidity(self, instrument: str) -> None:
        strategy = self.strategy_state
        pressure = event_pressure(self._active_events(), instrument)
        participation = strategy.flow_intensity
        participation *= pressure["order_arrival_multiplier"]
        participation += pressure["severity"] * strategy.shock_sensitivity * 0.25
        participation *= 0.5 + (strategy.aggression * 0.5)

        if self.random.random() > clamp(participation, 0.0, 1.0):
            return

        buy_bias = strategy.buy_bias
        buy_bias += pressure["directional_bias"] * strategy.shock_sensitivity * 0.35
        side = Side.BUY.value if self.random.random() < clamp(buy_bias, 0.0, 1.0) else Side.SELL.value
        quantity = self._order_quantity(instrument, side, pressure)
        if quantity <= 0:
            return

        order_id = await self.place_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET.value,
            explanation="AML liquidity taker market flow",
        )
        if order_id:
            self.logger.info(
                f"AMLLiquidityTaker {self.agent_id} placed {side} "
                f"market order for {quantity} {instrument}"
            )

    def _order_quantity(
        self,
        instrument: str,
        side: str,
        pressure: Mapping[str, float],
    ) -> int:
        strategy = self.strategy_state
        max_size = strategy.max_order_size
        max_size *= pressure["risk_limit_multiplier"]
        max_size *= clamp(pressure["order_arrival_multiplier"], 0.25, 2.0)
        quantity = self.random.randint(1, max(1, int(max_size)))

        current_position = self.long_qty[instrument] - self.short_qty[instrument]
        inventory_limit = max(0, int(strategy.inventory_limit * pressure["risk_limit_multiplier"]))
        if side == Side.BUY.value:
            return min(quantity, max(0, inventory_limit - current_position))

        held = self.long_qty[instrument]
        return min(quantity, held)
