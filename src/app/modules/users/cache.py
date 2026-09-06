"""User caching and cache invalidation infrastructure.

Collaborator for UserService (extracted per PR-05 / Finding #7).
Handles:
- User profile cache key generation (tenant-scoped and global)
- Profile caching and deserialization
- Single-user cache invalidation with token-version bump verification
- Batch-user cache invalidation in pipelined Redis operations
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.users.models import User
from src.app.modules.users.session_invalidation import (
    InvalidationReason,
    build_user_invalidation_keys,
    should_bump_token_version,
)

logger = get_logger(__name__)


# Cache configuration
USER_CACHE_TTL = 60  # seconds
USER_CACHE_PREFIX = "user:profile"
AUTH_INFO_CACHE_TTL = 3600  # 1 hour
AUTH_INFO_CACHE_PREFIX = "user:auth"


def _user_cache_key(user_id: UUID | str) -> str:
    """Generate cache key for user profile.

    User ID is globally unique (UUID), so no tenant scoping needed.
    """
    return f"{USER_CACHE_PREFIX}:{user_id}"


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


def _user_email_cache_key(email: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user by email (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes email to lowercase to prevent cache misses
    when the same email is provided with different casing.
    """
    t_id = _require_tenant_id(tenant_id)
    normalized_email = email.lower().strip()
    if t_id:
        return f"{USER_CACHE_PREFIX}:{t_id}:email:{normalized_email}"
    return f"{USER_CACHE_PREFIX}:global:email:{normalized_email}"


def _user_username_cache_key(username: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user by username (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes username to lowercase to prevent cache misses
    when the same username is provided with different casing.
    """
    t_id = _require_tenant_id(tenant_id)
    normalized_username = username.lower().strip()
    if t_id:
        return f"{USER_CACHE_PREFIX}:{t_id}:username:{normalized_username}"
    return f"{USER_CACHE_PREFIX}:global:username:{normalized_username}"


def _user_auth_cache_key(identifier: str, tenant_id: int | str | None = None) -> str:
    """Generate cache key for user auth info (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes identifier to lowercase to prevent cache misses
    when the same email/username is provided with different casing, consistent
    with _user_email_cache_key and _user_username_cache_key.
    """
    t_id = _require_tenant_id(tenant_id)
    normalized = identifier.lower().strip()
    if t_id:
        return f"{AUTH_INFO_CACHE_PREFIX}:{t_id}:{normalized}"
    return f"{AUTH_INFO_CACHE_PREFIX}:global:{normalized}"


class UserCacheInvalidator:
    """Collaborator for user profile and auth info caching and invalidation."""

    user_cache_key = staticmethod(_user_cache_key)
    user_email_cache_key = staticmethod(_user_email_cache_key)
    user_username_cache_key = staticmethod(_user_username_cache_key)
    user_auth_cache_key = staticmethod(_user_auth_cache_key)
    @staticmethod
    async def get_cached_user(cache_key: str) -> dict[str, Any] | None:
        """Get cached user data if available."""
        client = get_redis()
        if client is None:
            return None

        try:
            cached = await client.get(cache_key)
            if cached:
                result: dict[str, Any] = json_loads(cached)
                return result
        except Exception as e:
            logger.warning("Failed to get cached user", error=e)
        return None

    @staticmethod
    async def cache_user(user: User | dict[str, Any]) -> None:
        """Cache user profile in Redis."""
        client = get_redis()
        if client is None:
            return

        try:
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_data = user.copy()
                user_data.pop("hashed_password", None)
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
                tenant_id = user.get("tenant_id")
            else:
                # U7: Direct dict construction instead of Pydantic validate+dump
                # ORM data is already validated, skip redundant Pydantic overhead
                user_data = {
                    "id": user.id,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "profile_image_url": user.profile_image_url,
                    "tier_id": user.tier_id,
                    "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                    "is_superuser": user.is_superuser,
                    "is_email_verified": user.is_email_verified,
                    "is_active": user.is_active,
                }
                user_id = user.id
                email = user.email
                username = user.username
                tenant_id = user.tenant_id

            serialized = json_dumps(user_data)

            # Cache by all lookup keys using a pipeline for single round-trip
            async with client.pipeline(transaction=False) as pipe:
                pipe.setex(_user_cache_key(user_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_email_cache_key(email, tenant_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_username_cache_key(username, tenant_id), USER_CACHE_TTL, serialized)
                await pipe.execute()
        except Exception as e:
            user_id_log = user["id"] if isinstance(user, dict) else user.id
            logger.warning("Failed to cache user %s: %s", user_id_log, e)

    @staticmethod
    async def cache_user_auth_info(cache_key: str, user_with_tier: dict[str, Any]) -> None:
        """Cache user auth info in Redis."""
        client = get_redis()
        if client is None:
            return

        try:
            serialized = json_dumps(user_with_tier)
            await client.setex(cache_key, AUTH_INFO_CACHE_TTL, serialized)
        except Exception as e:
            logger.warning("Failed to cache auth info", error=e)

    @staticmethod
    async def invalidate_user_cache(
        user: User | dict[str, Any],
        bump_token_version: bool,
        reason: InvalidationReason,
        explicit_token_version: int | None = None,
    ) -> None:
        """Invalidate all cache entries for a user."""
        if bump_token_version != should_bump_token_version(reason):
            raise ValueError(
                f"bump_token_version={bump_token_version} does not match "
                f"should_bump_token_version(reason={reason!r}). "
                "Pass the bump value derived from the reason."
            )
        client = get_redis()
        if client is None:
            return

        try:
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
                tenant_id = user.get("tenant_id")
            else:
                user_id = user.id
                email = user.email
                username = user.username
                tenant_id = user.tenant_id

            # Fallback to get current tenant from context if tenant_id not provided
            if tenant_id is None:
                tenant_context = get_current_tenant()
                if tenant_context is not None:
                    tenant_id = tenant_context.tenant_id

            keys, token_keys = build_user_invalidation_keys(user_id, username, email, tenant_id)

            if keys:
                await client.delete(*keys)
            if token_keys:
                if explicit_token_version is not None:
                    new_version = explicit_token_version
                else:
                    if isinstance(user, dict):
                        current_version = user.get("token_version", 0)
                    else:
                        current_version = getattr(user, "token_version", 0)

                    new_version = current_version + 1 if bump_token_version else current_version

                async with client.pipeline(transaction=False) as pipe:
                    for key in token_keys:
                        pipe.set(key, str(new_version), ex=300)
                    await pipe.execute()
        except Exception as e:
            user_id_log = user["id"] if isinstance(user, dict) else user.id
            logger.warning("Failed to invalidate cache for user", user_id_log=user_id_log, error=e)

    @staticmethod
    async def invalidate_user_cache_batch(
        users: Sequence[Any],
        bump_token_version: bool = False,
        reason: InvalidationReason | None = None,
    ) -> None:
        """Invalidate cache entries for many users in ONE Redis pipeline."""
        if reason is not None and bump_token_version != should_bump_token_version(reason):
            raise ValueError(
                f"bump_token_version={bump_token_version} does not match should_bump_token_version(reason={reason!r})."
            )
        client = get_redis()
        if client is None or not users:
            return

        tenant_context = get_current_tenant()
        delete_keys: list[str] = []
        token_sets: list[tuple[str, int]] = []

        for user in users:
            try:
                if isinstance(user, dict):
                    user_id = user["id"]
                    email = user["email"]
                    username = user["username"]
                    tenant_id = user.get("tenant_id")
                    current_version = user.get("token_version", 0)
                elif hasattr(user, "id"):
                    user_id = user.id
                    email = user.email
                    username = user.username
                    tenant_id = user.tenant_id
                    current_version = getattr(user, "token_version", 0)
                else:
                    user_id = getattr(user, "id", user[0] if len(user) > 0 else None)
                    email = getattr(user, "email", user[1] if len(user) > 1 else None)
                    username = getattr(user, "username", user[2] if len(user) > 2 else None)
                    tenant_id = getattr(user, "tenant_id", user[3] if len(user) > 3 else None)
                    current_version = getattr(user, "token_version", user[4] if len(user) > 4 else 0)

                if tenant_id is None and tenant_context is not None:
                    tenant_id = tenant_context.tenant_id

                # Guard non-str/None values and empty strings since batch inputs
                # may be raw tuples or partial dicts. Lowercasing/stripping is
                # handled by build_user_invalidation_keys.
                norm_email = str(email).strip() or None if email is not None else None
                norm_username = str(username).strip() or None if username is not None else None

                u_del_keys, u_tok_keys = build_user_invalidation_keys(user_id, norm_username, norm_email, tenant_id)
                delete_keys.extend(u_del_keys)
                for tk in u_tok_keys:
                    token_sets.append((tk, current_version))
            except Exception as e:
                logger.warning("Failed to build batch-invalidation keys for user", error=e)

        if not delete_keys and not token_sets:
            return

        try:
            async with client.pipeline(transaction=False) as pipe:
                if delete_keys:
                    pipe.delete(*delete_keys)
                for key, version in token_sets:
                    new_version = version + 1 if bump_token_version else version
                    pipe.set(key, str(new_version), ex=300)
                await pipe.execute()
        except Exception as e:
            logger.warning("Failed to batch-invalidate user caches", count=len(users), error=e)


__all__ = [
    "AUTH_INFO_CACHE_PREFIX",
    "AUTH_INFO_CACHE_TTL",
    "USER_CACHE_PREFIX",
    "USER_CACHE_TTL",
    "UserCacheInvalidator",
    "_require_tenant_id",
    "_user_auth_cache_key",
    "_user_cache_key",
    "_user_email_cache_key",
    "_user_username_cache_key",
]
