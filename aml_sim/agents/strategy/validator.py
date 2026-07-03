"""Validation helpers for AML agent strategy state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class StrategyValidationError(ValueError):
    """Raised when an agent strategy state violates AML bounds."""


@dataclass(frozen=True)
class StrategyValidationLimits:
    """Boundaries for slow-loop-updated strategy state."""

    max_quote_size: int = 10_000
    max_quote_levels: int = 25
    max_retail_order_size: int = 10_000
    max_child_order_size: int = 100_000
    max_position: int = 10_000_000
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    allowed_risk_modes: tuple[str, ...] = ("conservative", "normal", "aggressive")
    allowed_alpha_strategies: tuple[str, ...] = (
        "target_execution",
        "momentum",
        "mean_reversion",
    )
    allowed_order_types: tuple[str, ...] = ("MARKET", "LIMIT")


DEFAULT_STRATEGY_LIMITS = StrategyValidationLimits()


def validate_strategy_state(
    strategy_state: Any,
    limits: StrategyValidationLimits = DEFAULT_STRATEGY_LIMITS,
) -> Any:
    """
    Validate a role-specific strategy state and return it unchanged.

    The validator checks fields by capability, so each agent can keep its own
    strategy dataclass while sharing common guardrails.
    """

    errors: list[str] = []

    _check_probability(strategy_state, "trade_probability", errors)
    _check_probability(strategy_state, "buy_bias", errors)
    _check_probability(strategy_state, "herding_tendency", errors)
    _check_probability(strategy_state, "panic_level", errors)
    _check_probability(strategy_state, "sentiment_sensitivity", errors)
    _check_probability(strategy_state, "shock_sensitivity", errors)
    _check_probability(strategy_state, "urgency", errors)
    _check_probability(strategy_state, "shock_reactivity", errors)
    _check_probability(strategy_state, "liquidity_withdrawal_sensitivity", errors)
    _check_probability(strategy_state, "information_edge", errors)
    _check_probability(strategy_state, "flow_intensity", errors)
    _check_probability(strategy_state, "aggression", errors)
    _check_probability(strategy_state, "momentum_weight", errors)

    if hasattr(strategy_state, "quote_size"):
        _check_upper_bound(
            strategy_state.quote_size,
            "quote_size",
            limits.max_quote_size,
            errors,
        )
    if hasattr(strategy_state, "quote_levels"):
        _check_upper_bound(
            strategy_state.quote_levels,
            "quote_levels",
            limits.max_quote_levels,
            errors,
        )
    if hasattr(strategy_state, "level_spacing") and strategy_state.level_spacing <= 0:
        errors.append("level_spacing must be greater than 0")
    _check_probability(strategy_state, "size_decay", errors)

    if hasattr(strategy_state, "spread") and strategy_state.spread <= 0:
        errors.append("spread must be greater than 0")
    if hasattr(strategy_state, "min_spread") and strategy_state.min_spread <= 0:
        errors.append("min_spread must be greater than 0")
    if hasattr(strategy_state, "max_spread") and strategy_state.max_spread <= 0:
        errors.append("max_spread must be greater than 0")
    if (
        hasattr(strategy_state, "min_spread")
        and hasattr(strategy_state, "max_spread")
        and strategy_state.min_spread > strategy_state.max_spread
    ):
        errors.append("min_spread must be <= max_spread")

    if hasattr(strategy_state, "max_order_size"):
        _check_upper_bound(
            strategy_state.max_order_size,
            "max_order_size",
            limits.max_retail_order_size,
            errors,
        )

    if hasattr(strategy_state, "child_order_size"):
        _check_upper_bound(
            strategy_state.child_order_size,
            "child_order_size",
            limits.max_child_order_size,
            errors,
        )
    if hasattr(strategy_state, "inventory_limit"):
        _check_position_bound(
            strategy_state.inventory_limit,
            "inventory_limit",
            limits.max_position,
            errors,
        )

    if hasattr(strategy_state, "lookback_ticks"):
        _check_upper_bound(strategy_state.lookback_ticks, "lookback_ticks", 10_000, errors)

    if hasattr(strategy_state, "entry_threshold") and strategy_state.entry_threshold < 0:
        errors.append("entry_threshold must be >= 0")
    if hasattr(strategy_state, "exit_threshold") and strategy_state.exit_threshold < 0:
        errors.append("exit_threshold must be >= 0")
    if hasattr(strategy_state, "signal_threshold") and strategy_state.signal_threshold < 0:
        errors.append("signal_threshold must be >= 0")
    if hasattr(strategy_state, "limit_offset") and strategy_state.limit_offset < 0:
        errors.append("limit_offset must be >= 0")
    if hasattr(strategy_state, "fair_value_anchor") and strategy_state.fair_value_anchor <= 0:
        errors.append("fair_value_anchor must be greater than 0")
    if hasattr(strategy_state, "fundamental_sensitivity") and strategy_state.fundamental_sensitivity < 0:
        errors.append("fundamental_sensitivity must be >= 0")

    if hasattr(strategy_state, "max_position"):
        _check_position_bound(strategy_state.max_position, "max_position", limits.max_position, errors)
    if hasattr(strategy_state, "min_position"):
        _check_position_bound(strategy_state.min_position, "min_position", limits.max_position, errors)
    if (
        hasattr(strategy_state, "min_position")
        and hasattr(strategy_state, "max_position")
        and strategy_state.min_position > strategy_state.max_position
    ):
        errors.append("min_position must be <= max_position")

    _check_has_attr(strategy_state, "confidence", errors)
    if hasattr(strategy_state, "confidence") and (
        strategy_state.confidence < limits.min_confidence
        or strategy_state.confidence > limits.max_confidence
    ):
        errors.append(
            f"confidence must be between {limits.min_confidence} and {limits.max_confidence}, "
            f"got {strategy_state.confidence}"
        )

    _check_has_attr(strategy_state, "risk_mode", errors)
    if hasattr(strategy_state, "risk_mode") and strategy_state.risk_mode not in limits.allowed_risk_modes:
        allowed = ", ".join(limits.allowed_risk_modes)
        errors.append(f"risk_mode must be one of [{allowed}], got {strategy_state.risk_mode!r}")

    if hasattr(strategy_state, "alpha_strategy"):
        alpha_strategy = str(strategy_state.alpha_strategy).lower()
        if alpha_strategy not in limits.allowed_alpha_strategies:
            allowed = ", ".join(limits.allowed_alpha_strategies)
            errors.append(f"alpha_strategy must be one of [{allowed}], got {alpha_strategy!r}")

    if hasattr(strategy_state, "alpha_strategies"):
        alpha_strategies = getattr(strategy_state, "alpha_strategies") or []
        for alpha_strategy in alpha_strategies:
            normalized = str(alpha_strategy).lower()
            if normalized not in limits.allowed_alpha_strategies:
                allowed = ", ".join(limits.allowed_alpha_strategies)
                errors.append(
                    f"alpha_strategies must contain only [{allowed}], got {normalized!r}"
                )

    if hasattr(strategy_state, "order_type"):
        order_type = str(strategy_state.order_type).upper()
        if order_type not in limits.allowed_order_types:
            allowed = ", ".join(limits.allowed_order_types)
            errors.append(f"order_type must be one of [{allowed}], got {order_type!r}")

    if errors:
        state_name = type(strategy_state).__name__
        raise StrategyValidationError(f"Invalid {state_name}: " + "; ".join(errors))

    return strategy_state


def _check_has_attr(strategy_state: Any, field_name: str, errors: list[str]) -> None:
    if not hasattr(strategy_state, field_name):
        errors.append(f"strategy state missing required field '{field_name}'")


def _check_probability(strategy_state: Any, field_name: str, errors: list[str]) -> None:
    if not hasattr(strategy_state, field_name):
        return

    value = getattr(strategy_state, field_name)
    if value < 0 or value > 1:
        errors.append(f"{field_name} must be between 0 and 1, got {value}")


def _check_upper_bound(
    value: int,
    field_name: str,
    upper_bound: int,
    errors: list[str],
) -> None:
    if value <= 0:
        errors.append(f"{field_name} must be greater than 0, got {value}")
    if value > upper_bound:
        errors.append(f"{field_name} must be <= {upper_bound}, got {value}")


def _check_position_bound(
    value: int,
    field_name: str,
    absolute_bound: int,
    errors: list[str],
) -> None:
    if abs(value) > absolute_bound:
        errors.append(f"{field_name} absolute value must be <= {absolute_bound}, got {value}")
