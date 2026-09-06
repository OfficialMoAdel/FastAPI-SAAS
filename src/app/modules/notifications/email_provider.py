"""Email provider interface and clients (re-exported from src.app.clients.notifications.smtp)."""

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
