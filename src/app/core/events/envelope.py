"""R2-01: typed domain event envelope over the string-based EventBus.

The existing :class:`~src.app.core.events.bus.EventBus` seam is
``publish(event_name: str, data: dict[str, Any])`` — payload typos surface at
runtime (or never), and no-handler publishes are silent. This module adds a
typed layer WITHOUT touching the string bus (strangler, additive):

- :class:`DomainEvent` — envelope binding a ``ClassVar`` event name to a
  Pydantic-validated payload model.
- :class:`TypedEventBus` — adapter implementing :class:`EventPublisher` over
  the existing bus. Serializes via ``model_dump()`` so the wire dict is
  byte-identical to today's hand-built dicts (webhook consumers see no change;
  the bus still injects ``_event_meta`` itself).
- :data:`typed_event_bus` — wraps the ``event_bus`` singleton. Safe across
  ``reset_event_bus()`` because that helper resets state IN PLACE on the same
  instance the wrapper holds.

Port one publish site at a time; string publishers keep working in parallel.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel

from src.app.core.events.bus import EventBus, event_bus
from src.app.core.events.priority import EventPriority

T = TypeVar("T", bound=BaseModel)


class DomainEvent(BaseModel, Generic[T]):
    """Typed envelope: a stable event name bound to a validated payload.

    Subclasses declare ``name`` (the wire event name, e.g. ``"user.created"``)
    and optionally ``priority`` as ClassVars; ``payload`` is the validated
    body. Pydantic validation at construction time is the gate that catches
    payload shape errors before publish, which the string bus cannot.
    """

    name: ClassVar[str]
    priority: ClassVar[EventPriority] = EventPriority.NORMAL

    payload: T

    def to_wire(self) -> tuple[str, dict[str, Any]]:
        """Return ``(event_name, payload_dict)`` for the string bus.

        ``model_dump()`` (not ``mode="json"``) keeps values exactly as the
        legacy hand-built dicts carried them (ints stay ints), so downstream
        serialization — including the webhook HMAC's
        ``json.dumps(sort_keys=True)`` — is unchanged.
        """
        return self.name, self.payload.model_dump()


class EventPublisher(Protocol):
    """Seam services depend on instead of the concrete bus."""

    async def publish_event(self, event: DomainEvent[Any], *, event_id: str | None = None) -> None:
        """Publish a typed domain event."""
        ...


class TypedEventBus:
    """Adapter publishing :class:`DomainEvent` envelopes onto an EventBus.

    Delegates to the wrapped bus's ``publish`` — idempotency, priority
    routing, tenant capture and ``_event_meta`` enrichment all stay in the
    existing bus; this layer only adds construction-time payload validation
    and a typed call site.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def publish_event(self, event: DomainEvent[Any], *, event_id: str | None = None) -> None:
        name, payload = event.to_wire()
        await self._bus.publish(name, payload, priority=event.priority, event_id=event_id)


# Wraps the singleton (not a copy): reset_event_bus() mutates the instance in
# place, so this reference stays valid across test resets.
typed_event_bus: EventPublisher = TypedEventBus(event_bus)
