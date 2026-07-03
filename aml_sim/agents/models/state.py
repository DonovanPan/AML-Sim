"""Strategy state models for AML agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
    min_spread: float = 0.05
    max_spread: float = 2.0
    quote_size: int = 100
    quote_levels: int = 1
    level_spacing: float = 0.05
    size_decay: float = 1.0
    target_inventory: int = 0
    inventory_skew: float = 0.001
    min_inventory: int = 0
    max_inventory: int = 20_000
    volatility_sensitivity: float = 4.0
    shock_spread_multiplier: float = 1.0
    shock_price_adjustment: float = 0.5
    liquidity_withdrawal_sensitivity: float = 0.25


@dataclass
class RetailStrategyState(BaseStrategyState):
    """Role-specific strategy state for an AML retail trader."""

    strategy_type: str = "retail_noise"
    trade_probability: float = 0.3
    buy_bias: float = 0.5
    max_order_size: int = 25
    herding_tendency: float = 0.0
    panic_level: float = 0.0
    sentiment_sensitivity: float = 0.4
    shock_sensitivity: float = 0.4


@dataclass
class InstitutionalStrategyState(BaseStrategyState):
    """Role-specific strategy state for an AML institutional trader."""

    strategy_type: str = "target_execution"
    target_positions: dict[str, int] = field(default_factory=dict)
    child_order_size: int = 100
    order_type: str = "MARKET"
    limit_price: Optional[float] = None
    execution_style: str = "sliced"
    urgency: float = 0.5
    alpha_strategy: str = "target_execution"
    alpha_strategies: list[str] = field(default_factory=list)
    strategy_weights: dict[str, float] = field(default_factory=dict)
    lookback_ticks: int = 5
    entry_threshold: float = 0.002
    exit_threshold: float = 0.0005
    max_position: int = 500
    min_position: int = 0
    signal_strength: float = 0.0
    shock_reactivity: float = 0.5


@dataclass
class InformedStrategyState(BaseStrategyState):
    """Strategy state for a participant with a private/fundamental signal."""

    strategy_type: str = "informed_value"
    fair_value_anchor: float = 100.0
    information_edge: float = 0.7
    trade_probability: float = 0.35
    max_order_size: int = 50
    max_position: int = 750
    min_position: int = 0
    signal_threshold: float = 0.002
    momentum_weight: float = 0.2
    fundamental_sensitivity: float = 1.0
    shock_reactivity: float = 0.8
    order_type: str = "MARKET"
    limit_offset: float = 0.03
    signal_strength: float = 0.0


@dataclass
class LiquidityTakerStrategyState(BaseStrategyState):
    """Strategy state for aggressive flow that consumes displayed liquidity."""

    strategy_type: str = "liquidity_taking"
    flow_intensity: float = 0.35
    buy_bias: float = 0.5
    max_order_size: int = 40
    inventory_limit: int = 500
    shock_sensitivity: float = 0.7
    aggression: float = 0.75
