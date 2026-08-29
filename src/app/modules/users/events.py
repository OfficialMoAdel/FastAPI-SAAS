"""R2-01: typed domain events for the users module.

Each event binds the wire event name (consumed by the webhook adapter and
audit handlers) to a Pydantic-validated payload, so shape errors fail at
construction instead of at runtime in a background task.

Wire contract: ``model_dump()`` output must stay byte-identical to the legacy
hand-built dicts — webhook consumers HMAC-sign ``json.dumps(sort_keys=True)``
of this payload, so field names and value types are frozen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.app.core.events.envelope import DomainEvent
from src.app.core.events.priority import EventPriority


class UserCreatedPayload(BaseModel):
    """Payload for ``user.created`` — frozen wire contract."""

    user_id: int = Field(..., description="Primary key of the created user")
    email: str = Field(..., description="User email address")
    username: str | None = Field(None, description="Username (optional)")
    tenant_id: str = Field(..., description="Tenant UUID as string")


class UserCreated(DomainEvent[UserCreatedPayload]):
    """A user was created and committed."""

    name = "user.created"
    priority = EventPriority.NORMAL
