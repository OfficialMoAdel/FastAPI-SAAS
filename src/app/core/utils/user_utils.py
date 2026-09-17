"""User utilities for attribute and representation access."""

from typing import Any


def get_user_attr(user: Any, attr: str, default: Any = None) -> Any:
    """Read a field off a CRUD-row shaped value (dict or ORM/object row).

    This is the accessor for database/cache rows whose shape varies by
    call path (FastCRUD ``schema_to_select`` dicts vs ORM objects) — NOT
    the principal accessor. The authenticated principal crosses the
    module seam as the typed :class:`AuthenticatedUser`
    (``src.app.core.principal``) and is read via attribute access;
    transitional ``dict | AuthenticatedUser`` unions (passkey payloads,
    profile resolution) are the only other sanctioned uses.
    """
    if isinstance(user, dict):
        return user.get(attr, default)
    return getattr(user, attr, default)
