"""Principal seam — the ONE typed identity crossing the module boundary.

This module is the canonical home of :class:`AuthenticatedUser`, the
validated principal every router hands to module services. It lives in
core (not ``api.deps``) deliberately: ``api.deps.auth`` imports the
module facades (``users``, ``api_keys``), so services importing the
principal from ``api.deps`` would close an import cycle — the reason
``users/service.py`` and ``webhooks/service.py`` carried ``TYPE_CHECKING``
workarounds. Importing from here is cycle-free in both directions.

Layers
------
- ``api.deps.auth`` re-exports :class:`AuthenticatedUser` unchanged, so
  existing ``from src.app.api.deps[.auth] import AuthenticatedUser``
  sites keep working; new code prefers this module.
- ``RequestContext.user`` carries this type; services take it directly
  instead of ``Any`` / ``dict | AuthenticatedUser`` and read attributes
  instead of re-deriving via the dict-or-object shim.
- :func:`get_user_attr` (``core.utils.user_utils``) remains for CRUD-row
  access (FastCRUD ``schema_to_select`` dicts / ORM objects) — it is no
  longer the principal accessor.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthenticatedUser(BaseModel):
    """Strict typed user model for request dependencies.

    Provides IDE autocomplete while maintaining backward compatibility
    with dict-based access via __getitem__.
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True, from_attributes=True)

    id: int
    uuid: UUID | str | None = None
    email: str
    username: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_deleted: bool = False
    tier_id: int | None = None
    tenant_id: UUID | str | int | None = None
    profile_image_url: str | None = None
    token_version: int = 0
    tier: dict[str, Any] | None = None
    api_key_id: str | int | None = None
    api_key_scopes: list[str] | None = None

    # Q-M2: dict-compat shim (__getitem__/get/keys/__contains__/__len__)
    # deleted. Principal call sites use attribute access; CRUD-row dicts
    # go through get_user_attr. Do NOT re-add subscript access — it hid
    # type errors on the hottest auth path.


__all__ = ["AuthenticatedUser"]
