"""Shared signal helpers for AML trading strategies."""

from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Any, Mapping


def clamp(value: float, lower: float, upper: float) -> float:
    """Return value clipped to the inclusive lower/upper range."""
    return max(lower, min(upper, value))


def price_series(
    price_history: Mapping[str, list[Mapping[str, Any]]],
    instrument: str,
    fallback_price: float | None = None,
) -> list[float]:
    """Extract clean positive prices for one instrument."""
    values: list[float] = []
    for row in price_history.get(instrument, []):
        try:
            price = float(row.get("price", 0))
        except (TypeError, ValueError):
            continue
        if price > 0:
            values.append(price)

    if not values and fallback_price is not None and fallback_price > 0:
        values.append(float(fallback_price))
    return values


def momentum_signal(prices: list[float], lookback_ticks: int) -> float:
    """Simple lookback return: positive means upward momentum."""
    if len(prices) < 2:
        return 0.0
    lookback = max(1, min(lookback_ticks, len(prices) - 1))
    start = prices[-lookback - 1]
    end = prices[-1]
    if start <= 0:
        return 0.0
    return (end / start) - 1.0


def mean_reversion_signal(prices: list[float], lookback_ticks: int) -> float:
    """Deviation from recent mean: positive means price is below recent mean."""
    if len(prices) < 2:
        return 0.0
    lookback = max(2, min(lookback_ticks, len(prices)))
    window = prices[-lookback:]
    avg_price = mean(window)
    last_price = window[-1]
    if avg_price <= 0:
        return 0.0
    return (avg_price - last_price) / avg_price


def realized_volatility(prices: list[float], lookback_ticks: int) -> float:
    """Estimate realized volatility from recent simple returns."""
    if len(prices) < 3:
        return 0.0
    lookback = max(2, min(lookback_ticks, len(prices) - 1))
    window = prices[-lookback - 1 :]
    returns = []
    for left, right in zip(window, window[1:]):
        if left > 0:
            returns.append((right / left) - 1.0)
    if len(returns) < 2:
        return abs(returns[0]) if returns else 0.0
    avg_return = mean(returns)
    variance = sum((item - avg_return) ** 2 for item in returns) / (len(returns) - 1)
    return sqrt(max(variance, 0.0))


def event_applies_to(event: Mapping[str, Any], instrument: str) -> bool:
    """Check whether an AML event should affect an instrument."""
    affected = event.get("affected_instruments") or event.get("instruments") or []
    if not affected:
        return True
    return instrument in affected


def event_pressure(events: list[Mapping[str, Any]], instrument: str) -> dict[str, float]:
    """
    Aggregate active event pressure for an instrument.

    direction is expected to be -1, 0, or +1. The returned directional_bias is
    severity-weighted and clipped so strategy code can safely add it to signals.
    """
    max_severity = 0.0
    directional_bias = 0.0
    fundamental_price_shift = 0.0
    order_arrival_multiplier = 1.0
    risk_limit_multiplier = 1.0
    liquidity_multiplier = 1.0

    for event in events:
        if not event_applies_to(event, instrument):
            continue
        try:
            severity = clamp(float(event.get("severity", 0.0)), 0.0, 1.0)
        except (TypeError, ValueError):
            severity = 0.0
        try:
            direction = clamp(float(event.get("direction", 0.0)), -1.0, 1.0)
        except (TypeError, ValueError):
            direction = 0.0

        max_severity = max(max_severity, severity)
        directional_bias += severity * direction
        fundamental_price_shift += _event_float(event, "fundamental_price_shift", 0.0)
        order_arrival_multiplier *= _event_float(event, "order_arrival_multiplier", 1.0)
        risk_limit_multiplier = min(
            risk_limit_multiplier,
            _event_float(event, "risk_limit_multiplier", 1.0),
        )
        liquidity_multiplier = min(
            liquidity_multiplier,
            _event_float(event, "liquidity_multiplier", 1.0),
        )

    return {
        "severity": clamp(max_severity, 0.0, 1.0),
        "directional_bias": clamp(directional_bias, -1.0, 1.0),
        "fundamental_price_shift": fundamental_price_shift,
        "order_arrival_multiplier": clamp(order_arrival_multiplier, 0.05, 5.0),
        "risk_limit_multiplier": clamp(risk_limit_multiplier, 0.05, 2.0),
        "liquidity_multiplier": clamp(liquidity_multiplier, 0.05, 2.0),
    }


def _event_float(event: Mapping[str, Any], key: str, default: float) -> float:
    try:
        return float(event.get(key, default))
    except (TypeError, ValueError):
        return default


def target_from_signal(
    signal: float,
    *,
    current_target: int,
    entry_threshold: float,
    exit_threshold: float,
    max_position: int,
    min_position: int,
) -> int:
    """Convert an alpha signal into a target inventory."""
    if abs(signal) <= exit_threshold:
        return 0
    if signal >= entry_threshold:
        return max_position
    if signal <= -entry_threshold:
        return min_position
    return current_target
