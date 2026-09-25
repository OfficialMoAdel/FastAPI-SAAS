"""The user cache key family, in one place.

Every key that can hold a cached copy of a user is built here. Readers and
invalidators both import from this module, so a prefix or normalisation
change lands once and every reader and delete follows it.

This module exists as its own file for a concrete reason: ``users.cache``
imports ``users.session_invalidation`` (for the invalidation policy), so an
invalidation that imported the key builders back out of ``users.cache``
would close a cycle. The key family is a leaf — it depends on the tenant
context and nothing else in the module — so it lives alone.

Two scopes exist for every identity key. A reader writes whichever scope its
own tenant context produced: the tenant-scoped form when a tenant is active,
otherwise the ``:global:`` form. An invalidation cannot know which one a
reader wrote, so :func:`build_user_invalidation_delete_keys` returns both.
Deleting a key that was never written is a no-op; deleting only the
tenant-scoped form left a globally-keyed entry stale until its TTL.

The ``token_version`` keys are NOT here. That family is written by
``core.security.jwt_service`` and is spelled by the invalidation module,
which owns the transition that produces them.
"""

from __future__ import annotations

from uuid import UUID

from src.app.core.tenancy.context import get_current_tenant

USER_CACHE_PREFIX = "user:profile"
AUTH_INFO_CACHE_PREFIX = "user:auth"


def _require_tenant_id(tenant_id: int | str | None = None) -> str | None:
    """Get current tenant ID if available, without raising when called outside tenant context.

    Tenant context is used for tenant-scoped cache keys to prevent
    cross-tenant cache collisions. When running in pre-auth or global context,
    returns None to allow safe fallback.
    """
    if tenant_id is not None:
        return str(tenant_id)

    tenant = get_current_tenant()
    if tenant is not None:
        return str(tenant.id)
    return None


def _user_cache_key(user_id: int | str | UUID) -> str:
    """Cache key for a user profile by id.

    A user id is globally unique, so no tenant scoping needed.
    """
    return f"{USER_CACHE_PREFIX}:{user_id}"


def _email_key(email: str, tenant: str | None) -> str:
    """Cache key for a user by email, at an explicit scope. No ambient lookup."""
    normalized_email = email.lower().strip()
    if tenant:
        return f"{USER_CACHE_PREFIX}:{tenant}:email:{normalized_email}"
    return f"{USER_CACHE_PREFIX}:global:email:{normalized_email}"


def _username_key(username: str, tenant: str | None) -> str:
    """Cache key for a user by username, at an explicit scope. No ambient lookup."""
    normalized_username = username.lower().strip()
    if tenant:
        return f"{USER_CACHE_PREFIX}:{tenant}:username:{normalized_username}"
    return f"{USER_CACHE_PREFIX}:global:username:{normalized_username}"


def _auth_info_key(identifier: str, tenant: str | None) -> str:
    """Cache key for a user's auth info, at an explicit scope. No ambient lookup."""
    normalized = identifier.lower().strip()
    if tenant:
        return f"{AUTH_INFO_CACHE_PREFIX}:{tenant}:{normalized}"
    return f"{AUTH_INFO_CACHE_PREFIX}:global:{normalized}"


def _user_email_cache_key(email: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user by email (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes email to lowercase to prevent cache misses
    when the same email is provided with different casing.
    """
    return _email_key(email, _require_tenant_id(tenant_id))


def _user_username_cache_key(username: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user by username (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes username to lowercase to prevent cache misses
    when the same username is provided with different casing.
    """
    return _username_key(username, _require_tenant_id(tenant_id))


def _user_auth_cache_key(identifier: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user auth info (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes identifier to lowercase to prevent cache misses
    when the same email/username is provided with different casing, consistent
    with _user_email_cache_key and _user_username_cache_key.
    """
    return _auth_info_key(identifier, _require_tenant_id(tenant_id))


def build_user_invalidation_delete_keys(
    user_id: int | str | UUID,
    username: str | None = None,
    email: str | None = None,
    tenant_id: int | str | None = None,
) -> list[str]:
    """Every cache key that can hold a copy of this user.

    The single owner of the delete side of this key family, so an invalidation
    never spells a key.
    """
    keys = [_user_cache_key(user_id)]
    tenant = str(tenant_id) if tenant_id is not None else None
    # Both email and username get an auth-info key: auth_info() is keyed on
    # whichever identifier the caller passed, so either may have been written.
    for value, identity_key in ((email, _email_key), (username, _username_key)):
        if not value:
            continue
        keys.append(identity_key(value, tenant))
        keys.append(_auth_info_key(value, tenant))
        if tenant is not None:
            # The other scope, for a reader that ran without this tenant.
            keys.append(identity_key(value, None))
            keys.append(_auth_info_key(value, None))
    return keys


__all__ = [
    "AUTH_INFO_CACHE_PREFIX",
    "USER_CACHE_PREFIX",
    "_auth_info_key",
    "_email_key",
    "_require_tenant_id",
    "_user_auth_cache_key",
    "_user_cache_key",
    "_user_email_cache_key",
    "_user_username_cache_key",
    "_username_key",
    "build_user_invalidation_delete_keys",
]
