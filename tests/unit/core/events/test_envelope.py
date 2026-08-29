"""R2-01: typed EventBus envelope tests.

Validates the typed layer without Redis/ARQ: a fake bus records what the
TypedEventBus adapter forwards, proving payload validation, wire identity,
priority pass-through, and event_id forwarding.
"""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.app.core.events.envelope import DomainEvent, TypedEventBus
from src.app.core.events.priority import EventPriority


class _Payload(BaseModel):
    user_id: int
    email: str


class _UserCreated(DomainEvent[_Payload]):
    name = "user.created"
    priority = EventPriority.NORMAL


class _CriticalEvent(DomainEvent[_Payload]):
    name = "critical.thing"
    priority = EventPriority.CRITICAL


class FakeBus:
    """Records publish calls; mirrors the EventBus.publish signature."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        priority: EventPriority = EventPriority.NORMAL,
        event_id: str | None = None,
    ) -> None:
        self.calls.append(
            {"event_name": event_name, "data": data, "priority": priority, "event_id": event_id}
        )


class TestDomainEvent:
    def test_payload_validation_rejects_bad_shape(self):
        """The gate: wrong types fail at construction, not in a background task."""
        with pytest.raises(ValidationError):
            _UserCreated(payload=_Payload(user_id="not-an-int", email="x@y.com"))  # type: ignore[arg-type]

    def test_to_wire_matches_legacy_dict(self):
        """model_dump output is byte-identical to the hand-built legacy dict."""
        legacy = {"user_id": 7, "email": "a@b.com"}
        event = _UserCreated(payload=_Payload(**legacy))
        name, wire = event.to_wire()
        assert name == "user.created"
        assert wire == legacy

    def test_default_priority_is_normal(self):
        assert _UserCreated.priority == EventPriority.NORMAL


class TestTypedEventBus:
    @pytest.mark.asyncio
    async def test_publish_forwards_name_payload_priority(self):
        fake = FakeBus()
        bus = TypedEventBus(fake)  # type: ignore[arg-type]
        await bus.publish_event(_UserCreated(payload=_Payload(user_id=1, email="a@b.com")))

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["event_name"] == "user.created"
        assert call["data"] == {"user_id": 1, "email": "a@b.com"}
        assert call["priority"] == EventPriority.NORMAL
        assert call["event_id"] is None

    @pytest.mark.asyncio
    async def test_publish_passes_event_id_for_idempotency(self):
        fake = FakeBus()
        bus = TypedEventBus(fake)  # type: ignore[arg-type]
        await bus.publish_event(
            _UserCreated(payload=_Payload(user_id=1, email="a@b.com")),
            event_id="user-created-1",
        )
        assert fake.calls[0]["event_id"] == "user-created-1"

    @pytest.mark.asyncio
    async def test_publish_uses_event_priority(self):
        fake = FakeBus()
        bus = TypedEventBus(fake)  # type: ignore[arg-type]
        await bus.publish_event(_CriticalEvent(payload=_Payload(user_id=1, email="a@b.com")))
        assert fake.calls[0]["priority"] == EventPriority.CRITICAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
