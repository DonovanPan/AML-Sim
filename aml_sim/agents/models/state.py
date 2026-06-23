"""Strategy state models for AML agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from utils.orders import OrderType


@dataclass
class BaseStrategyState:
    """Common strategy metadata updated by AML slow strategists."""

    strategy_type: str
    risk_mode: str = "normal"
    confidence: float = 1.0
    reason: str | None = None
    updated_at: str | None = None


@dataclass
class MarketMakerStrategyState(BaseStrategyState):
    """Role-specific strategy state for an AML market maker."""

    strategy_type: str = "market_making"
    fair_price: float = 100.0
    spread: float = 0.2
    quote_size: int = 100
    target_inventory: int = 0
    inventory_skew: float = 0.001


@dataclass
class RetailStrategyState(BaseStrategyState):
    """Role-specific strategy state for an AML retail trader."""

    strategy_type: str = "retail_noise"
    trade_probability: float = 0.3
    buy_bias: float = 0.5
    max_order_size: int = 25
    herding_tendency: float = 0.0
    panic_level: float = 0.0


@dataclass
class InstitutionalStrategyState(BaseStrategyState):
    """Role-specific strategy state for an AML institutional trader."""

    strategy_type: str = "target_execution"
    target_positions: dict[str, int] = field(default_factory=dict)
    child_order_size: int = 100
    order_type: str = OrderType.MARKET.value
    limit_price: Optional[float] = None
    execution_style: str = "sliced"
    urgency: float = 0.5
