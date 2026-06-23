"""Memory backends for AML agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol


DEFAULT_MEMORY_BACKEND = "local"
DEFAULT_MEMORY_LIMIT = 10


class MemoryBackend(Protocol):
    """Interface shared by local and external AML memory backends."""

    def add_event(
        self,
        agent_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        timestamp: Optional[datetime | str] = None,
    ) -> None:
        """Store an agent memory event."""

    def retrieve_context(
        self,
        agent_id: str,
        *,
        observation: Optional[Mapping[str, Any]] = None,
        limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> dict[str, Any]:
        """Retrieve memory context to include in an LLM prompt."""


@dataclass
class MemoryEvent:
    """A lightweight memory event stored by AML memory backends."""

    event_type: str
    payload: dict[str, Any]
    timestamp: Optional[str] = None


@dataclass
class LocalAgentMemory:
    """Simple in-process memory backend for offline AML prototypes."""

    events_by_agent: dict[str, list[MemoryEvent]] = field(default_factory=dict)

    def add_event(
        self,
        agent_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        timestamp: Optional[datetime | str] = None,
    ) -> None:
        event = MemoryEvent(
            event_type=event_type,
            payload=_serialize_mapping(payload),
            timestamp=_serialize_value(timestamp),
        )
        self.events_by_agent.setdefault(agent_id, []).append(event)

    def retrieve_context(
        self,
        agent_id: str,
        *,
        observation: Optional[Mapping[str, Any]] = None,
        limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> dict[str, Any]:
        events = self.events_by_agent.get(agent_id, [])
        recent_events = events[-limit:] if limit > 0 else []

        return {
            "backend": "local",
            "agent_id": agent_id,
            "recent_events": [_serialize_value(event) for event in recent_events],
            "event_count": len(events),
            "retrieval_note": (
                "Local memory returns the most recent events only. "
                "Use the Zep backend later for semantic/graph retrieval."
            ),
        }


class ZepAgentMemory:
    """
    Placeholder for a future Zep-backed memory backend.

    Expected future config shape:
        memory:
          backend: zep
          api_key_env: ZEP_API_KEY
          collection: aml-sim
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.config = dict(config or {})
        raise NotImplementedError(
            "Zep memory backend is not implemented yet. Use memory.backend='local' "
            "for now, then wire this class to the Zep SDK when API-key-backed "
            "memory is ready."
        )

    def add_event(
        self,
        agent_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        timestamp: Optional[datetime | str] = None,
    ) -> None:
        raise NotImplementedError("Zep memory backend is not implemented yet.")

    def retrieve_context(
        self,
        agent_id: str,
        *,
        observation: Optional[Mapping[str, Any]] = None,
        limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> dict[str, Any]:
        raise NotImplementedError("Zep memory backend is not implemented yet.")


def create_memory_backend(config: Optional[Mapping[str, Any]] = None) -> MemoryBackend:
    """
    Create a memory backend from an AML config mapping.

    Supported now:
        {"backend": "local"}

    Reserved for later:
        {"backend": "zep", "api_key_env": "ZEP_API_KEY"}
    """

    config = dict(config or {})
    backend = str(config.get("backend", DEFAULT_MEMORY_BACKEND)).lower()

    if backend == "local":
        return LocalAgentMemory()
    if backend == "zep":
        return ZepAgentMemory(config)

    raise ValueError(f"Unsupported AML memory backend: {backend!r}")


def _serialize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _serialize_value(item) for key, item in value.items()}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _serialize_mapping(asdict(value))
    if isinstance(value, Mapping):
        return _serialize_mapping(value)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value
