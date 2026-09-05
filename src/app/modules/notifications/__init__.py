"""Notification module for managing email and system notifications."""

from src.app.modules.notifications.email_provider import (
    EmailAttachment,
    EmailMessage,
    EmailProvider,
    InMemoryEmailProvider,
    SMTPProvider,
    get_email_provider,
    reset_email_provider,
    set_email_provider,
)
from src.app.modules.notifications.models import (
    EmailTemplateOverride,
    Notification,
    NotificationStatus,
)
from src.app.modules.notifications.service import NotificationService

__all__ = [
    "EmailAttachment",
    "EmailMessage",
    "EmailProvider",
    "EmailTemplateOverride",
    "InMemoryEmailProvider",
    "Notification",
    "NotificationService",
    "NotificationStatus",
    "SMTPProvider",
    "get_email_provider",
    "reset_email_provider",
    "set_email_provider",
]
