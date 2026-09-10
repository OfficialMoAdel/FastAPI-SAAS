"""Deprecated alias for the canonical email provider surface.

.. deprecated::
    Import from ``src.app.clients.notifications`` (canonical implementation:
    ``src.app.clients.notifications.smtp``) instead. This shim exists for one
    release for downstream consumers still on the old import path and will be
    removed afterwards.

Attribute access is lazy (PEP 562): importing this module is warning-free,
and the ``DeprecationWarning`` fires only when a deprecated name is actually
used.
"""

from __future__ import annotations

# ruff: noqa: F822 - __all__ names resolve via __getattr__ (PEP 562 lazy shim)
import warnings
from typing import Any

_DEPRECATED_NAMES = frozenset(
    {
        "EmailAttachment",
        "EmailMessage",
        "EmailProvider",
        "InMemoryEmailProvider",
        "SMTPProvider",
        "get_email_provider",
        "reset_email_provider",
        "set_email_provider",
    }
)


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_NAMES:
        warnings.warn(
            "src.app.modules.notifications.email_provider is deprecated; "
            "use src.app.clients.notifications instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from src.app.clients.notifications import smtp as _smtp

        return getattr(_smtp, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
