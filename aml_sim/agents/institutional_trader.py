"""
AML Institutional Trader.

Models a larger participant that tries to build or reduce a target position in
child orders over time. Later this can become the LLM-directed strategy agent.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from aml_sim.agents.base import BaseAMLAgent
from aml_sim.agents.context.memory import MemoryBackend
from aml_sim.agents.context.observation import ObservationProcessor
from aml_sim.agents.models.profile import InstitutionalProfile, coerce_profile
from aml_sim.agents.strategy.llm_slow_strategy import (
    SlowStrategist,
    create_static_institutional_llm_strategist,
)
from aml_sim.agents.models.state import InstitutionalStrategyState
from aml_sim.agents.strategy.signals import (
    clamp,
    event_pressure,
    mean_reversion_signal,
    momentum_signal,
    price_series,
    target_from_signal,
)
from utils.orders import OrderType, Side


class AMLInstitutionalTrader(BaseAMLAgent):
    """
    Basic institutional-style participant for synthetic AML markets.

    Institutional here means larger capital, slower cadence, target inventory,
    and sliced execution rather than one giant order.
    """

    def __init__(
        self,
        instrument_exchange_map: Dict[str, str],
        target_positions: Optional[Dict[str, int]] = None,
        child_order_size: int = 100,
        order_type: str = OrderType.MARKET.value,
        limit_price: Optional[float] = None,
        alpha_strategy: str = "target_execution",
        alpha_strategies: Optional[list[str]] = None,
        strategy_weights: Optional[Dict[str, float]] = None,
        lookback_ticks: int = 5,
        entry_threshold: float = 0.002,
        exit_threshold: float = 0.0005,
        max_position: int = 500,
        min_position: int = 0,
        shock_reactivity: float = 0.5,
        profile: Optional[InstitutionalProfile | Mapping[str, Any]] = None,
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
            strategy_state=InstitutionalStrategyState(
                target_positions=dict(target_positions or {}),
                child_order_size=child_order_size,
                order_type=order_type.upper(),
                limit_price=limit_price,
                alpha_strategy=alpha_strategy,
                alpha_strategies=self._normalize_alpha_strategies(
                    alpha_strategy,
                    alpha_strategies,
                ),
                strategy_weights=dict(strategy_weights or {}),
                lookback_ticks=lookback_ticks,
                entry_threshold=entry_threshold,
                exit_threshold=exit_threshold,
                max_position=max_position,
                min_position=min_position,
                shock_reactivity=shock_reactivity,
            ),
            profile=coerce_profile(profile, InstitutionalProfile),
            memory=memory,
            observation_processor=observation_processor,
            slow_strategist=slow_strategist or create_static_institutional_llm_strategist(),
            slow_loop_interval_seconds=slow_loop_interval_seconds,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **trader_kwargs,
        )

        self.logger.info(
            f"AMLInstitutionalTrader {self.agent_id} initialized: "
            f"strategy_state={self.strategy_state}"
        )

    async def run_fast_loop(self, observation: Mapping[str, Any]) -> None:
        for instrument in self.instrument_exchange_map.keys():
            self._update_alpha_target(instrument, observation)
            await self._execute_toward_target(instrument)

    def _normalize_alpha_strategies(
        self,
        alpha_strategy: str,
        alpha_strategies: Optional[list[str]],
    ) -> list[str]:
        strategies = [
            str(item).lower()
            for item in (alpha_strategies or [alpha_strategy])
            if str(item).strip()
        ]
        primary = str(alpha_strategy or "target_execution").lower()
        if primary and primary not in strategies:
            strategies.insert(0, primary)
        return strategies or ["target_execution"]

    def _update_alpha_target(self, instrument: str, observation: Mapping[str, Any]) -> None:
        strategy = self.strategy_state
        alpha_strategies = strategy.alpha_strategies or [strategy.alpha_strategy]
        alpha_strategies = [str(item).lower() for item in alpha_strategies]
        active_alpha_strategies = [
            item for item in alpha_strategies if item != "target_execution"
        ]
        if not active_alpha_strategies:
            return

        fallback_price = self.prices.get(instrument, 0)
        prices = price_series(self.price_history, instrument, fallback_price)
        signal = 0.0
        signal_parts: dict[str, float] = {}
        for alpha_strategy in active_alpha_strategies:
            weight = float(strategy.strategy_weights.get(alpha_strategy, 1.0))
            if alpha_strategy == "momentum":
                raw_signal = momentum_signal(prices, strategy.lookback_ticks)
            elif alpha_strategy == "mean_reversion":
                raw_signal = mean_reversion_signal(prices, strategy.lookback_ticks)
            else:
                self.logger.warning(
                    f"Unknown institutional alpha strategy {alpha_strategy!r}; skipping it."
                )
                continue
            weighted_signal = raw_signal * weight
            signal += weighted_signal
            signal_parts[alpha_strategy] = weighted_signal

        pressure = event_pressure(list(observation.get("events", [])), instrument)
        signal += pressure["directional_bias"] * strategy.shock_reactivity * strategy.entry_threshold
        if prices and prices[-1] > 0:
            signal += (
                pressure["fundamental_price_shift"]
                / prices[-1]
                * strategy.shock_reactivity
            )

        effective_max_position = max(
            strategy.min_position,
            int(strategy.max_position * pressure["risk_limit_multiplier"]),
        )
        effective_min_position = min(strategy.min_position, effective_max_position)

        current_target = strategy.target_positions.get(instrument, 0)
        if "target_execution" in alpha_strategies and abs(signal) <= strategy.exit_threshold:
            next_target = min(current_target, effective_max_position)
        else:
            next_target = target_from_signal(
                signal,
                current_target=current_target,
                entry_threshold=strategy.entry_threshold,
                exit_threshold=strategy.exit_threshold,
                max_position=effective_max_position,
                min_position=effective_min_position,
            )

        strategy.signal_strength = signal
        strategy.target_positions[instrument] = next_target
        if next_target != current_target:
            self.logger.info(
                f"AMLInstitutionalTrader {self.agent_id} alpha target update for {instrument}: "
                f"strategies={signal_parts}, combined_signal={signal:.6f}, "
                f"target {current_target} -> {next_target}"
            )

    async def _execute_toward_target(self, instrument: str) -> None:
        strategy = self.strategy_state
        target = strategy.target_positions.get(instrument, 0)
        current = self.long_qty[instrument] - self.short_qty[instrument]
        gap = target - current

        if gap == 0:
            return

        side = Side.BUY.value if gap > 0 else Side.SELL.value
        pressure = event_pressure(self._active_events(), instrument)
        child_size = strategy.child_order_size
        child_size *= clamp(pressure["order_arrival_multiplier"], 0.25, 2.0)
        child_size *= pressure["risk_limit_multiplier"]
        quantity = min(abs(gap), max(1, int(child_size)))

        if side == Side.SELL.value:
            held = self.long_qty[instrument]
            if held <= 0:
                self.logger.debug(f"Institutional trader skipped SELL for {instrument}: no inventory")
                return
            quantity = min(quantity, held)

        price = strategy.limit_price if strategy.order_type == OrderType.LIMIT.value else None
        order_id = await self.place_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=strategy.order_type,
            price=price,
            explanation=f"AML institutional child order toward target {target}",
        )
        if order_id:
            self.logger.info(
                f"AMLInstitutionalTrader {self.agent_id} placed {side} "
                f"{strategy.order_type} order for {quantity} {instrument} "
                f"(current={current}, target={target})"
            )
