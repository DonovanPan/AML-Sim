"""
AML Market Maker Trader.

Posts both bid and ask limit orders around a configurable fair price so an
orderbook scenario has synthetic liquidity without historical replay data.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from aml_sim.agents.base import BaseAMLAgent
from aml_sim.agents.context.memory import MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import MarketMakerProfile, coerce_profile
from aml_sim.agents.strategy.llm_slow_strategy import (
    SlowStrategist,
    create_static_market_maker_llm_strategist,
)
from aml_sim.agents.models.state import MarketMakerStrategyState
from aml_sim.agents.strategy.signals import event_pressure, price_series, realized_volatility
from utils.orders import OrderType, Side


class AMLMarketMakerTrader(BaseAMLAgent):
    """
    Simple synthetic market maker for AML simulations.

    On each action interval it:
    - cancels outstanding quotes from previous ticks,
    - posts a buy limit order below fair price,
    - posts a sell limit order above fair price,
    - nudges quotes based on inventory so it does not accumulate forever.
    """

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        fair_price: float = 100.0,
        spread: float = 0.2,
        min_spread: float = 0.05,
        max_spread: float = 2.0,
        quote_size: int = 100,
        quote_levels: int = 1,
        level_spacing: float = 0.05,
        size_decay: float = 1.0,
        inventory_skew: float = 0.001,
        target_inventory: int = 0,
        min_inventory: int = 0,
        max_inventory: int = 20_000,
        volatility_sensitivity: float = 4.0,
        shock_spread_multiplier: float = 1.0,
        shock_price_adjustment: float = 0.5,
        liquidity_withdrawal_sensitivity: float = 0.25,
        allow_short_selling: bool = False,
        profile: Optional[MarketMakerProfile | Mapping[str, Any]] = None,
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
            strategy_state=MarketMakerStrategyState(
                fair_price=fair_price,
                spread=spread,
                min_spread=min_spread,
                max_spread=max_spread,
                quote_size=quote_size,
                quote_levels=max(1, quote_levels),
                level_spacing=max(0.01, level_spacing),
                size_decay=max(0.0, min(1.0, size_decay)),
                target_inventory=target_inventory,
                inventory_skew=inventory_skew,
                min_inventory=min_inventory,
                max_inventory=max_inventory,
                volatility_sensitivity=volatility_sensitivity,
                shock_spread_multiplier=shock_spread_multiplier,
                shock_price_adjustment=shock_price_adjustment,
                liquidity_withdrawal_sensitivity=liquidity_withdrawal_sensitivity,
            ),
            profile=coerce_profile(profile, MarketMakerProfile),
            memory=memory,
            observation_processor=observation_processor,
            slow_strategist=slow_strategist or create_static_market_maker_llm_strategist(),
            slow_loop_interval_seconds=slow_loop_interval_seconds,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )
        self.allow_short_selling = allow_short_selling
        self.quote_order_ids: set[str] = set()

        self.logger.info(
            f"AMLMarketMakerTrader {self.agent_id} initialized: "
            f"strategy_state={self.strategy_state}"
        )

    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        await self._refresh_quotes()

    async def _refresh_quotes(self) -> None:
        await self._cancel_existing_quotes()

        for instrument in self.instrument_exchange_map.keys():
            quote_ladder = self._quote_ladder(instrument)
            for level, (bid_price, _ask_price, quantity) in enumerate(quote_ladder, start=1):
                await self._place_bid(instrument, bid_price, quantity, level)

            ask_capacity = None
            if not self.allow_short_selling:
                ask_capacity = max(
                    0,
                    self.long_qty[instrument] - self.strategy_state.min_inventory,
                )
            for level, (_bid_price, ask_price, quantity) in enumerate(quote_ladder, start=1):
                if ask_capacity is not None:
                    quantity = min(quantity, ask_capacity)
                    ask_capacity -= quantity
                if quantity <= 0:
                    break
                await self._place_ask(instrument, ask_price, quantity, level)

    async def _cancel_existing_quotes(self) -> None:
        for order_id in list(self.quote_order_ids):
            if order_id in self.pending_orders:
                await self.cancel_order(order_id)
            self.quote_order_ids.discard(order_id)

    def _quote_prices(self, instrument: str) -> tuple[float, float]:
        strategy = self.strategy_state
        inventory = self.long_qty[instrument] - self.short_qty[instrument]
        inventory_gap = inventory - strategy.target_inventory
        skew = inventory_gap * strategy.inventory_skew
        pressure = event_pressure(self._active_events(), instrument)
        prices = price_series(self.price_history, instrument, self.prices.get(instrument, strategy.fair_price))
        volatility = realized_volatility(prices, lookback_ticks=10)

        shock_mid_adjustment = (
            pressure["fundamental_price_shift"]
            + pressure["directional_bias"] * strategy.shock_price_adjustment
        )
        midpoint = max(0.01, strategy.fair_price - skew + shock_mid_adjustment)
        dynamic_spread = strategy.spread
        dynamic_spread *= 1 + (volatility * strategy.volatility_sensitivity)
        dynamic_spread *= 1 + (pressure["severity"] * strategy.shock_spread_multiplier)
        dynamic_spread = min(strategy.max_spread, max(strategy.min_spread, dynamic_spread))
        half_spread = max(0.01, dynamic_spread / 2)
        bid = max(0.01, midpoint - half_spread)
        ask = max(bid + 0.01, midpoint + half_spread)
        return round(bid, 2), round(ask, 2)

    def _quote_ladder(self, instrument: str) -> list[tuple[float, float, int]]:
        strategy = self.strategy_state
        best_bid, best_ask = self._quote_prices(instrument)
        levels: list[tuple[float, float, int]] = []
        seen_bids: set[float] = set()
        seen_asks: set[float] = set()
        for level in range(max(1, strategy.quote_levels)):
            offset = strategy.level_spacing * level
            bid = round(max(0.01, best_bid - offset), 2)
            ask = round(max(bid + 0.01, best_ask + offset), 2)
            quantity = max(1, int(self._quote_size(instrument) * (strategy.size_decay ** level)))
            if bid in seen_bids or ask in seen_asks:
                continue
            seen_bids.add(bid)
            seen_asks.add(ask)
            levels.append((bid, ask, quantity))
        return levels

    async def _place_bid(self, instrument: str, price: float, quantity: int, level: int) -> None:
        max_inventory = self._effective_max_inventory(instrument)
        current_inventory = self.long_qty[instrument] - self.short_qty[instrument]
        if current_inventory >= max_inventory:
            self.logger.debug(f"Skipping bid for {instrument}: max inventory reached")
            return
        quantity = min(quantity, max(0, max_inventory - current_inventory))
        if quantity <= 0:
            return

        order_id = await self.place_order(
            instrument=instrument,
            side=Side.BUY.value,
            quantity=quantity,
            order_type=OrderType.LIMIT.value,
            price=price,
            explanation=f"AML market maker bid quote L{level}",
        )
        if order_id:
            self.quote_order_ids.add(order_id)

    async def _place_ask(self, instrument: str, price: float, quantity: int, level: int) -> None:
        held = self.long_qty[instrument]
        if held <= self.strategy_state.min_inventory and not self.allow_short_selling:
            self.logger.debug(f"Skipping ask for {instrument}: min inventory reached")
            return

        quantity = min(quantity, held) if not self.allow_short_selling else quantity
        if quantity <= 0:
            self.logger.debug(f"Skipping ask for {instrument}: no inventory to sell")
            return

        order_id = await self.place_order(
            instrument=instrument,
            side=Side.SELL.value,
            quantity=quantity,
            order_type=OrderType.LIMIT.value,
            price=price,
            explanation=f"AML market maker ask quote L{level}",
            is_short=self.allow_short_selling and held <= 0,
        )
        if order_id:
            self.quote_order_ids.add(order_id)

    def _quote_size(self, instrument: str) -> int:
        strategy = self.strategy_state
        pressure = event_pressure(self._active_events(), instrument)
        size_multiplier = 1 - (pressure["severity"] * strategy.liquidity_withdrawal_sensitivity)
        size_multiplier *= pressure["liquidity_multiplier"]
        size_multiplier *= pressure["risk_limit_multiplier"]
        return max(1, int(strategy.quote_size * max(0.05, size_multiplier)))

    def _effective_max_inventory(self, instrument: str) -> int:
        pressure = event_pressure(self._active_events(), instrument)
        return max(
            self.strategy_state.min_inventory,
            int(self.strategy_state.max_inventory * pressure["risk_limit_multiplier"]),
        )

    async def on_trade_execution(self, msg: Dict[str, Any]) -> None:
        await super().on_trade_execution(msg)
        self.logger.debug(
            f"AMLMarketMakerTrader {self.agent_id} inventory after trade: "
            f"{dict(self.long_qty)}"
        )
