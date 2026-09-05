"""Notification event dispatch helper.

Allows decoupled event-based notification requests without cross-module imports.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.app.core.events import EventPriority, event_bus


async def request_notification(
    to_email: str,
    template_name: str,
    context: dict[str, Any] | None = None,
    tenant_id: UUID | str | None = None,
    user_id: int | None = None,
    priority: EventPriority = EventPriority.NORMAL,
) -> None:
    """Standardized entrypoint to dispatch a notification via the event bus.

    Decouples callers from direct NotificationService construction and
    database/SMTP operations, routing through the transactional outbox seam.

    Args:
        to_email: Recipient email address
        template_name: Template name (e.g. 'email_verification', 'password_reset', 'magic_link')
        context: Template rendering context variables
        tenant_id: Optional tenant UUID or string
        user_id: Optional user ID for user preferences / audit
        priority: Event priority (default NORMAL)
    """
    payload: dict[str, Any] = {
        "to_email": to_email,
        "template_name": template_name,
        "context": context or {},
        "tenant_id": str(tenant_id) if tenant_id else None,
        "user_id": user_id,
    }
    await event_bus.publish("notification.request", payload, priority=priority or EventPriority.NORMAL)


__all__ = ["request_notification"]
