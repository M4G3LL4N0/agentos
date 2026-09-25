"""Lightweight persisted event system for AgentOS.

Events are recorded to the store so the full execution history survives
restarts and can be inspected. There is deliberately no streaming
infrastructure: a single process-local bus plus durable rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from agentos.models import Event, EventType, new_id, utc_now_iso
from agentos.store import Store


class EventBus:
    """Emit and query events. Emits are persisted synchronously."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._subscribers: dict[EventType, list[Callable[[Event], None]]] = defaultdict(list)
        self.subscriber_errors: list[dict[str, str]] = []

    def emit(
        self,
        event_type: EventType | str,
        objective_id: str | None = None,
        execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        event = Event(
            id=new_id("evt"),
            event_type=event_type,
            objective_id=objective_id,
            execution_id=execution_id,
            payload=dict(payload or {}),
            created_at=utc_now_iso(),
        )
        self.store.save_event(event)
        for subscriber in self._subscribers.get(event_type, []):
            try:
                subscriber(event)
            except Exception as exc:
                self.subscriber_errors.append(
                    {"eventId": event.id, "error": f"{type(exc).__name__}: {exc}"}
                )
        return event

    def subscribe(
        self, event_type: EventType | str, subscriber: Callable[[Event], None]
    ) -> None:
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        if subscriber not in self._subscribers[event_type]:
            self._subscribers[event_type].append(subscriber)

    def for_objective(self, objective_id: str, limit: int = 200) -> list[Event]:
        return self.store.list_events(objective_id=objective_id, limit=limit)

    def recent(self, limit: int = 200) -> list[Event]:
        return self.store.list_events(limit=limit)