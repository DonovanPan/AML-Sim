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
    max_child_order_size: int = 100_000
    min_confidence: float = 0.0
    allowed_risk_modes: tuple[str, ...] = ("conservative", "normal", "aggressive")


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

    if hasattr(strategy_state, "quote_size"):
        _check_upper_bound(
            strategy_state.quote_size,
            "quote_size",
            limits.max_quote_size,
            errors,
        )

    if hasattr(strategy_state, "spread") and strategy_state.spread <= 0:
        errors.append("spread must be greater than 0")

    if hasattr(strategy_state, "child_order_size"):
        _check_upper_bound(
            strategy_state.child_order_size,
            "child_order_size",
            limits.max_child_order_size,
            errors,
        )

    if strategy_state.confidence < limits.min_confidence:
        errors.append(
            f"confidence must be at least {limits.min_confidence}, "
            f"got {strategy_state.confidence}"
        )

    if strategy_state.risk_mode not in limits.allowed_risk_modes:
        allowed = ", ".join(limits.allowed_risk_modes)
        errors.append(f"risk_mode must be one of [{allowed}], got {strategy_state.risk_mode!r}")

    if errors:
        state_name = type(strategy_state).__name__
        raise StrategyValidationError(f"Invalid {state_name}: " + "; ".join(errors))

    return strategy_state


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
