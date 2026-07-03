"""LLM-backed slow-loop strategist for AML agents."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, fields, is_dataclass, replace
from typing import Any, Mapping, Optional, Protocol

from aml_sim.agents.models.profile import profile_to_dict


class SlowStrategist(Protocol):
    """Interface for AML slow-loop strategists."""

    def propose(
        self,
        observation: Mapping[str, Any],
        current_strategy: Any,
        **kwargs: Any,
    ) -> Any:
        """Return a proposed strategy state for validation and application."""


class LLMStrategistConfigurationError(RuntimeError):
    """Raised when the LLM strategist is used without a configured client."""


class LLMStrategyResponseError(ValueError):
    """Raised when an LLM response cannot be parsed into a strategy proposal."""


class JSONLLMClient(Protocol):
    """Minimal protocol expected from an LLM client adapter."""

    async def complete_json(self, context: Mapping[str, Any]) -> Mapping[str, Any] | str:
        """Return a JSON object or JSON string containing strategy updates."""


class LLMStrategist:
    """
    Slow-loop strategist that asks an LLM for strategy-state updates.

    The strategist never places orders. It only proposes an updated strategy
    state, which should be passed through the strategy validator before use.
    """

    def __init__(
        self,
        client: Optional[JSONLLMClient] = None,
        *,
        allowed_strategy_fields: Optional[set[str]] = None,
    ) -> None:
        self.client = client
        self.allowed_strategy_fields = allowed_strategy_fields

    async def propose(
        self,
        observation: Mapping[str, Any],
        current_strategy: Any,
        *,
        profile: Optional[Mapping[str, Any]] = None,
        memory: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """
        Ask the LLM to propose a new strategy state from context.

        Expected LLM JSON shape:
            {
              "strategy_updates": {"risk_mode": "conservative"},
              "confidence": 0.7,
              "reason": "Inventory is elevated after recent fills."
            }
        """

        if self.client is None:
            raise LLMStrategistConfigurationError(
                "LLMStrategist requires a JSONLLMClient. Provide a client adapter "
                "from config before enabling the LLM slow loop."
            )

        context = self.build_context(
            observation=observation,
            profile=profile,
            memory=memory,
            current_strategy=current_strategy,
        )
        raw_response = await self.client.complete_json(context)
        response = self._parse_response(raw_response)
        updates = self._extract_strategy_updates(response)

        return self._apply_updates(
            current_strategy=current_strategy,
            updates=updates,
            observation=observation,
            response=response,
        )

    def build_context(
        self,
        *,
        observation: Mapping[str, Any],
        current_strategy: Any,
        profile: Optional[Mapping[str, Any]] = None,
        memory: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build the structured context sent to the LLM client."""

        return {
            "task": "Propose strategy_state updates only. Do not place orders.",
            "output_contract": {
                "strategy_updates": "object containing only existing strategy fields",
                "confidence": "optional float",
                "reason": "optional short explanation",
            },
            "profile": profile_to_dict(profile),
            "memory": dict(memory or observation.get("memory", {}) or {}),
            "observation": dict(observation),
            "current_strategy": self._strategy_to_dict(current_strategy),
        }

    def _parse_response(self, raw_response: Mapping[str, Any] | str) -> dict[str, Any]:
        if isinstance(raw_response, Mapping):
            return dict(raw_response)

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise LLMStrategyResponseError(
                f"LLM response was not valid JSON: {exc}"
            ) from exc

        if not isinstance(parsed, Mapping):
            raise LLMStrategyResponseError("LLM response JSON must be an object.")

        return dict(parsed)

    def _extract_strategy_updates(self, response: Mapping[str, Any]) -> dict[str, Any]:
        updates = response.get("strategy_updates", response)
        if not isinstance(updates, Mapping):
            raise LLMStrategyResponseError("strategy_updates must be a JSON object.")
        return dict(updates)

    def _apply_updates(
        self,
        *,
        current_strategy: Any,
        updates: Mapping[str, Any],
        observation: Mapping[str, Any],
        response: Mapping[str, Any],
    ) -> Any:
        allowed_fields = self._allowed_fields(current_strategy)
        clean_updates = {
            key: value
            for key, value in updates.items()
            if key in allowed_fields
        }

        if "confidence" in response and "confidence" in allowed_fields:
            clean_updates["confidence"] = response["confidence"]
        if "reason" in response and "reason" in allowed_fields:
            clean_updates["reason"] = response["reason"]
        if "updated_at" in allowed_fields:
            clean_updates.setdefault("updated_at", observation.get("current_time"))

        if is_dataclass(current_strategy):
            return replace(current_strategy, **clean_updates)

        # Non-dataclass: copy first so a later validation failure does not
        # corrupt the live strategy object.
        import copy

        proposed = copy.deepcopy(current_strategy)
        for key, value in clean_updates.items():
            setattr(proposed, key, value)
        return proposed

    def _allowed_fields(self, current_strategy: Any) -> set[str]:
        if self.allowed_strategy_fields is not None:
            return set(self.allowed_strategy_fields)
        if is_dataclass(current_strategy):
            return {field.name for field in fields(current_strategy)}
        return set(vars(current_strategy).keys())

    def _strategy_to_dict(self, strategy: Any) -> dict[str, Any]:
        if is_dataclass(strategy):
            return asdict(strategy)
        if isinstance(strategy, Mapping):
            return dict(strategy)
        return dict(vars(strategy))


class StaticJSONLLMClient:
    """Tiny test client that returns a fixed JSON response."""

    def __init__(self, response: Mapping[str, Any] | str) -> None:
        self.response = response
        self.last_context: Optional[Mapping[str, Any]] = None

    async def complete_json(self, context: Mapping[str, Any]) -> Mapping[str, Any] | str:
        self.last_context = context
        if inspect.isawaitable(self.response):
            return await self.response
        return self.response


STATIC_MARKET_MAKER_RESPONSE = {
    "strategy_updates": {
        "risk_mode": "normal",
        "spread": 0.25,
        "quote_size": 100,
        "inventory_skew": 0.0015,
    },
    "confidence": 0.75,
    "reason": "Static market-maker LLM test response: quote slightly wider and manage inventory conservatively.",
}


STATIC_RETAIL_RESPONSE = {
    "strategy_updates": {
        "risk_mode": "normal",
        "trade_probability": 0.35,
        "buy_bias": 0.52,
        "herding_tendency": 0.15,
        "panic_level": 0.05,
    },
    "confidence": 0.7,
    "reason": "Static retail LLM test response: slightly active, mildly bullish, low panic.",
}


STATIC_INSTITUTIONAL_RESPONSE = {
    "strategy_updates": {
        "risk_mode": "normal",
        "child_order_size": 100,
        "execution_style": "sliced",
        "urgency": 0.6,
    },
    "confidence": 0.78,
    "reason": "Static institutional LLM test response: keep sliced execution with moderate urgency.",
}


STATIC_INFORMED_RESPONSE = {
    "strategy_updates": {
        "risk_mode": "normal",
        "trade_probability": 0.38,
        "information_edge": 0.72,
    },
    "confidence": 0.74,
    "reason": "Static informed-trader LLM test response: keep trading only when the private value signal is strong.",
}


STATIC_LIQUIDITY_TAKER_RESPONSE = {
    "strategy_updates": {
        "risk_mode": "normal",
        "flow_intensity": 0.38,
        "aggression": 0.75,
    },
    "confidence": 0.7,
    "reason": "Static liquidity-taker LLM test response: maintain steady aggressive flow with bounded size.",
}


def create_static_market_maker_llm_strategist() -> LLMStrategist:
    """Create a fixed-response LLM strategist for market-maker path tests."""

    return LLMStrategist(client=StaticJSONLLMClient(STATIC_MARKET_MAKER_RESPONSE))


def create_static_retail_llm_strategist() -> LLMStrategist:
    """Create a fixed-response LLM strategist for retail path tests."""

    return LLMStrategist(client=StaticJSONLLMClient(STATIC_RETAIL_RESPONSE))


def create_static_institutional_llm_strategist() -> LLMStrategist:
    """Create a fixed-response LLM strategist for institutional path tests."""

    return LLMStrategist(client=StaticJSONLLMClient(STATIC_INSTITUTIONAL_RESPONSE))


def create_static_informed_llm_strategist() -> LLMStrategist:
    """Create a fixed-response LLM strategist for informed-trader path tests."""

    return LLMStrategist(client=StaticJSONLLMClient(STATIC_INFORMED_RESPONSE))


def create_static_liquidity_taker_llm_strategist() -> LLMStrategist:
    """Create a fixed-response LLM strategist for liquidity-taker path tests."""

    return LLMStrategist(client=StaticJSONLLMClient(STATIC_LIQUIDITY_TAKER_RESPONSE))
