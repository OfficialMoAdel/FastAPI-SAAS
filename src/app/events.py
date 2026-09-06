"""Application-level event handler wiring.

Composition root for registering module event handlers with the central event bus.
Located at application level (outside core/) so core infrastructure remains
strictly decoupled from feature modules.
"""

from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)


def register_application_event_handlers(bus: Any) -> None:
    """Pre-register all application module event handlers with the event bus."""
    try:
        from src.app.modules.audit_logs.service import handle_audit_log_event
        from src.app.modules.notifications.service import (
            handle_notification_request_event,
            handle_notification_send_event,
        )
        from src.app.modules.webhooks.service import handle_webhook_trigger_event, webhook_event_adapter

        bus.register_handler("audit.log", handle_audit_log_event)
        bus.register_handler("notification.send", handle_notification_send_event)
        bus.register_handler("notification.request", handle_notification_request_event)
        bus.register_handler("webhook.trigger", handle_webhook_trigger_event)

        business_events = [
            "user.created",
            "user.deleted",
            "user.updated",
            "api_key.created",
            "api_key.deleted",
            "sso.login",
            "auth.mfa.enabled",
            "auth.mfa.disabled",
            "auth.login",
            "auth.logout",
        ]
        for evt in business_events:
            bus.register_handler(evt, webhook_event_adapter)

        logger.info(
            "Event handler registration complete",
            total_handlers=sum(len(handlers) for handlers in bus._handlers.values()),
        )
        bus._registration_complete = True
    except Exception as e:
        logger.error(
            "Failed to register event handlers during startup",
            error=str(e),
            exc_info=True,
        )
        raise
