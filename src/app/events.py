"""Application-level event handler wiring.

Composition root for registering module event handlers with the central event bus.
Located at application level (outside core/) so core infrastructure remains
strictly decoupled from feature modules.
"""

from src.app.core.events.bus import EventBus, EventHandler
from src.app.core.logger import get_logger

logger = get_logger(__name__)


def register_application_event_handlers(bus: EventBus) -> None:
    """Register all application handlers as one readiness-controlled batch."""
    from src.app.modules.audit_logs.service import handle_audit_log_event, handle_login_issued_event
    from src.app.modules.notifications.service import handle_notification_request_event
    from src.app.modules.webhooks.service import handle_webhook_trigger_event, webhook_event_adapter

    handlers: list[tuple[str, EventHandler]] = [
        ("audit.log", handle_audit_log_event),
        ("auth.login_issued", handle_login_issued_event),
        ("notification.request", handle_notification_request_event),
        ("webhook.trigger", handle_webhook_trigger_event),
    ]
    business_events = (
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
    )
    handlers.extend((event_name, webhook_event_adapter) for event_name in business_events)

    try:
        total_handlers = bus.register_handlers(handlers)
    except Exception as error:
        logger.error(
            "Failed to register event handlers during startup",
            error=str(error),
            exc_info=True,
        )
        raise

    logger.info("Event handler registration complete", total_handlers=total_handlers)
