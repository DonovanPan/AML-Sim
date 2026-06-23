"""Agent profile models for AML behavioral context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Mapping, TypeVar


@dataclass
class AgentProfile:
    """Stable identity and behavioral context for an AML agent."""

    role: str
    name: str | None = None
    risk_tolerance: str = "medium"
    decision_style: str = "balanced"
    personality: dict[str, Any] = field(default_factory=dict)
    behavioral_traits: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    custom: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketMakerProfile(AgentProfile):
    """Profile defaults for a market-making liquidity provider."""

    role: str = "market_maker"
    risk_tolerance: str = "medium"
    decision_style: str = "inventory_aware_quoting"
    inventory_discipline: float = 0.7
    quote_aggressiveness: float = 0.5


@dataclass
class RetailProfile(AgentProfile):
    """Profile defaults for a small noisy retail participant."""

    role: str = "retail"
    risk_tolerance: str = "medium"
    decision_style: str = "noisy_sentiment_driven"
    social_sensitivity: float = 0.5
    panic_sensitivity: float = 0.4
    herding_tendency: float = 0.4


@dataclass
class InstitutionalProfile(AgentProfile):
    """Profile defaults for a larger target-execution participant."""

    role: str = "institutional"
    risk_tolerance: str = "medium"
    decision_style: str = "target_execution"
    execution_patience: float = 0.6
    benchmark_focus: str = "arrival_price"
    information_sensitivity: float = 0.5


ProfileT = TypeVar("ProfileT", bound=AgentProfile)


def coerce_profile(
    profile: AgentProfile | Mapping[str, Any] | None,
    profile_cls: type[ProfileT],
) -> ProfileT:
    """Create a role-specific profile from YAML/config data."""

    if profile is None:
        return profile_cls()
    if isinstance(profile, profile_cls):
        return profile
    if is_dataclass(profile):
        profile = asdict(profile)
    if not isinstance(profile, Mapping):
        raise TypeError(f"profile must be a mapping or {profile_cls.__name__}")

    valid_fields = {field.name for field in fields(profile_cls)}
    known_values = {
        key: value
        for key, value in profile.items()
        if key in valid_fields
    }
    custom_values = {
        key: value
        for key, value in profile.items()
        if key not in valid_fields
    }
    if custom_values:
        known_values["custom"] = {
            **dict(known_values.get("custom", {})),
            **custom_values,
        }

    return profile_cls(**known_values)


def profile_to_dict(profile: AgentProfile | Mapping[str, Any] | None) -> dict[str, Any]:
    """Serialize a profile into the dict shape passed to LLM context."""

    if profile is None:
        return {}
    if is_dataclass(profile):
        return asdict(profile)
    return dict(profile)
