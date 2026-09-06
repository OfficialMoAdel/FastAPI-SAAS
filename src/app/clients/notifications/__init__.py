"""Notification and email integration clients."""

from src.app.clients.notifications.smtp import (
    EmailAttachment,
    EmailMessage,
    EmailProvider,
    InMemoryEmailProvider,
    SMTPProvider,
    get_email_provider,
    reset_email_provider,
    set_email_provider,
)

__all__ = [
    "EmailAttachment",
    "EmailMessage",
    "EmailProvider",
    "InMemoryEmailProvider",
    "SMTPProvider",
    "get_email_provider",
    "reset_email_provider",
    "set_email_provider",
]
