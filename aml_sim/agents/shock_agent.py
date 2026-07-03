"""AML shock/event injector agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from agents.agent import Agent
from utils.messages import MessageType
from utils.time_utils import parse_datetime_utc


class AMLShockAgent(Agent):
    """
    Emits synthetic market shocks to AML agents during a simulation.

    Shock messages use StockSim's existing STATUS_UPDATE message type so the
    StockSim submodule does not need a new enum value for AML-owned events.
    """

    def __init__(
        self,
        scheduled_events: Optional[list[Mapping[str, Any]]] = None,
        target_agent_ids: Optional[list[str]] = None,
        default_duration_ticks: int = 10,
        agent_id: Optional[str] = None,
        rabbitmq_host: str = "localhost",
        **_: Any,
    ) -> None:
        super().__init__(agent_id=agent_id, rabbitmq_host=rabbitmq_host)
        self.scheduled_events = [dict(event) for event in (scheduled_events or [])]
        self.target_agent_ids = list(target_agent_ids or [])
        self.default_duration_ticks = default_duration_ticks
        self.emitted_event_ids: set[str] = set()

    async def _handle_regular_message(self, msg: dict[str, Any]) -> None:
        self.logger.debug(f"AMLShockAgent ignored message: {msg.get('type')}")

    async def handle_time_tick(self, payload: dict[str, Any]) -> None:
        await super().handle_time_tick(payload)
        for index, event in enumerate(self.scheduled_events):
            event_id = str(event.get("id") or event.get("shock_id") or f"shock_{index}")
            if event_id in self.emitted_event_ids:
                continue
            if self._event_due(event, payload):
                await self._emit_event(event_id, event, payload)

    def _event_due(self, event: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        tick_id = payload.get("tick_id")
        event_tick = event.get("tick", event.get("tick_id"))
        if event_tick is not None and tick_id is not None:
            try:
                return int(tick_id) >= int(event_tick)
            except (TypeError, ValueError):
                return False

        event_time = event.get("at") or event.get("time")
        current_time = payload.get("current_time")
        if event_time and current_time:
            try:
                return parse_datetime_utc(str(current_time)) >= parse_datetime_utc(str(event_time))
            except ValueError:
                return False

        return False

    async def _emit_event(
        self,
        event_id: str,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        shock_payload = self._build_payload(event_id, event, payload)
        if not self.target_agent_ids:
            self.logger.warning(f"AMLShockAgent has no targets for shock {event_id}")
            self.emitted_event_ids.add(event_id)
            return

        for target_agent_id in self.target_agent_ids:
            await self.send_message(
                target_agent_id,
                MessageType.STATUS_UPDATE,
                shock_payload,
            )

        self.emitted_event_ids.add(event_id)
        self.logger.info(
            f"AMLShockAgent emitted {event_id} to {len(self.target_agent_ids)} targets: "
            f"type={shock_payload['shock_type']}, severity={shock_payload['severity']}, "
            f"direction={shock_payload['direction']}"
        )

    def _build_payload(
        self,
        event_id: str,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        current_time = payload.get("current_time")
        timestamp = current_time
        if isinstance(current_time, datetime):
            timestamp = current_time.isoformat()

        severity = _clamp(_safe_float(event.get("severity", 0.5), 0.5), 0.0, 1.0)
        direction = _clamp(_safe_float(event.get("direction", 0.0), 0.0), -1.0, 1.0)
        fundamental_shift = _safe_float(
            event.get("fundamental_price_shift"),
            direction
            * severity
            * _safe_float(event.get("fundamental_price_shift_per_severity"), 1.0),
        )
        order_arrival_multiplier = _safe_float(
            event.get("order_arrival_multiplier"),
            1.0 + (severity * 1.25),
        )
        if "risk_limit_multiplier" in event:
            risk_limit_multiplier = _safe_float(event.get("risk_limit_multiplier"), 1.0)
        elif direction < 0:
            risk_limit_multiplier = 1.0 - (severity * 0.55)
        else:
            risk_limit_multiplier = 1.0 + (severity * 0.25)

        liquidity_multiplier = _safe_float(
            event.get("liquidity_multiplier"),
            1.0 - (severity * 0.5),
        )

        return {
            "event_type": "AML_SHOCK",
            "shock_id": event_id,
            "shock_type": event.get("shock_type", event.get("type", "generic_shock")),
            "timestamp": timestamp,
            "tick_id": payload.get("tick_id"),
            "emitted_tick_id": payload.get("tick_id"),
            "severity": severity,
            "direction": direction,
            "fundamental_price_shift": round(fundamental_shift, 4),
            "order_arrival_multiplier": round(_clamp(order_arrival_multiplier, 0.05, 5.0), 4),
            "risk_limit_multiplier": round(_clamp(risk_limit_multiplier, 0.05, 2.0), 4),
            "liquidity_multiplier": round(_clamp(liquidity_multiplier, 0.05, 2.0), 4),
            "affected_instruments": list(event.get("affected_instruments", event.get("instruments", []))),
            "duration_ticks": int(event.get("duration_ticks", self.default_duration_ticks)),
            "message": event.get("message", ""),
        }


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
