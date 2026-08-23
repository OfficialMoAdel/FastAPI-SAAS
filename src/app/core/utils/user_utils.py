"""User utilities for attribute and representation access."""

from typing import Any


def get_user_attr(user: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute or key from user object (dict or Pydantic model/object).

    Provides seamless compatibility between dict-based user representations (Phase 2)
    and object/Pydantic-based AuthenticatedUser representations (Phase 3).
    """
    if isinstance(user, dict):
        return user.get(attr, default)
    return getattr(user, attr, default)
