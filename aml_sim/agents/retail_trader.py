"""
AML Retail Trader.

Models a small, noisy participant that trades occasionally with small market
orders. Later this agent can react to synthetic news and herding signals.
"""

import random
from typing import Any, Dict, Optional

from aml_sim.agents.base import AMLTraderAgent
from utils.orders import OrderType, Side


class AMLRetailTrader(AMLTraderAgent):
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
        random_seed: Optional[int] = None,
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **kwargs,
    ) -> None:
        super().__init__(
            instrument_exchange_map=instrument_exchange_map,
            agent_id=agent_id,
            rabbitmq_host=rabbitmq_host,
            **kwargs,
        )

        if not (0.0 <= trade_probability <= 1.0):
            raise ValueError(
                f"trade_probability must be between 0.0 and 1.0, got {trade_probability}"
            )
        if max_order_size < 1:
            raise ValueError(f"max_order_size must be at least 1, got {max_order_size}")
        if not (0.0 <= buy_bias <= 1.0):
            raise ValueError(f"buy_bias must be between 0.0 and 1.0, got {buy_bias}")

        self.trade_probability = trade_probability
        self.max_order_size = max_order_size
        self.buy_bias = buy_bias
        self.random = random.Random(random_seed)

        self.logger.info(
            f"AMLRetailTrader {self.agent_id} initialized: "
            f"trade_probability={self.trade_probability}, "
            f"max_order_size={self.max_order_size}, buy_bias={self.buy_bias}"
        )

    async def handle_time_tick(self, payload: Dict[str, Any]) -> None:
        await super().handle_time_tick(payload)

        current_time = self.current_time
        if self.next_action_time is None:
            self.next_action_time = current_time

        if current_time >= self.next_action_time:
            for instrument in self.instrument_exchange_map.keys():
                await self._maybe_trade(instrument)
            self.next_action_time = current_time + self.action_interval

    async def _maybe_trade(self, instrument: str) -> None:
        if self.random.random() > self.trade_probability:
            return

        quantity = self.random.randint(1, self.max_order_size)
        side = Side.BUY.value if self.random.random() < self.buy_bias else Side.SELL.value

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
