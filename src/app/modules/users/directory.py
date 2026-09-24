"""UserDirectory — narrow read Interface over user identity.

Deep-module split of the ``UserService`` read surface: every auth-gated or
membership lookup (by id/username/email, tier-eager or cached) lives here
behind typed returns, while ``UserService`` keeps writes, privileged
management flows (``_get_active_user``), and listing/aggregation. External
readers must use this, not the service or ``crud_users``.

Return contracts
----------------
- Tier-eager auth payloads (``auth_info`` / ``active`` / ``member``) return
  :class:`UserAuthInfo` — the full ``get_user_with_tier_eager`` dict
  (``hashed_password``, ``token_version``, nested ``tier``) as a model.
- Plain lookups (``by_id`` / ``by_username`` / ``by_email`` / ``profile``) return
  :class:`UserRead`, exactly as ``UserService.get`` / ``get_by_username``
  validated before.

Caching moves with the reads: auth-payload caching
(``_user_auth_cache_key``) and plain-lookup caching (``_user_cache_key`` /
``_user_username_cache_key``) are owned here, via ``UserCacheInvalidator``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.modules.users.cache import (
    UserCacheInvalidator,
    _user_auth_cache_key,
    _user_cache_key,
    _user_email_cache_key,
    _user_username_cache_key,
)
from src.app.modules.users.crud import crud_users
from src.app.modules.users.schemas import UserAuthInfo, UserRead
from src.app.shared.exceptions import ModelNotFoundError

logger = get_logger(__name__)


class UserDirectory:
    """Narrow read Interface over user identity (auth payloads + lookups)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def auth_info(
        self, *, username: str | None = None, email: str | None = None, tenant_id: Any = None
    ) -> UserAuthInfo | None:
        """Tier-eager auth payload, cached (moved from ``UserService.get_user_auth_info``).

        ``tenant_id`` scopes both the cache key and the DB lookup, matching
        ``active`` / ``member``. ``None`` falls back to the ambient tenant
        context (pre-auth callers); callers that already hold a tenant must
        pass it explicitly so the key never collapses to ``global:``.
        """
        identifier = email if email else username
        if not identifier:
            return None

        cache_key = _user_auth_cache_key(identifier, tenant_id)

        cached = await UserCacheInvalidator.get_cached_user(cache_key)
        if cached:
            return UserAuthInfo.model_validate(cached)

        user_with_tier = await crud_users.get_user_with_tier_eager(
            db=self.db, username=username, email=email, tenant_id=tenant_id
        )
        if not user_with_tier:
            return None

        await UserCacheInvalidator.cache_user_auth_info(cache_key, user_with_tier)
        return UserAuthInfo.model_validate(user_with_tier)

    async def active(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        tenant_id: Any = None,
    ) -> UserAuthInfo | None:
        """Non-deleted, active, tenant-scoped user (moved from ``UserService.get_active_user``)."""
        user = await crud_users.get_user_with_tier_eager(
            db=self.db,
            id=id,
            username=username,
            email=email,
            require_active=True,
            tenant_id=tenant_id,
        )
        return UserAuthInfo.model_validate(user) if user else None

    async def member(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        tenant_id: Any = None,
    ) -> UserAuthInfo | None:
        """Non-deleted, tenant-scoped user without the active gate (moved from
        ``UserService.get_tenant_user``) — role bookkeeping for onboarding /
        cleanup flows."""
        user = await crud_users.get_user_with_tier_eager(
            db=self.db,
            id=id,
            username=username,
            email=email,
            require_active=False,
            tenant_id=tenant_id,
        )
        return UserAuthInfo.model_validate(user) if user else None

    async def by_id(self, user_id: Any) -> UserRead | None:
        """Cached lookup by ID (moved from ``UserService.get``)."""
        cached = await UserCacheInvalidator.get_cached_user(_user_cache_key(user_id))
        if cached:
            logger.debug("Cache hit for user", user_id=user_id)
            return UserRead.model_validate(cached)

        user = await crud_users.get(db=self.db, id=user_id, is_deleted=False)
        if user:
            await UserCacheInvalidator.cache_user(user)
            return UserRead.model_validate(user)
        return None

    async def by_username(self, username: str) -> UserRead:
        """Cached lookup by username, raises when missing (moved from
        ``UserService.get_by_username``)."""
        cached = await UserCacheInvalidator.get_cached_user(_user_username_cache_key(username))
        if cached:
            logger.debug("Cache hit for username", username=username)
            return UserRead.model_validate(cached)

        db_user = await crud_users.get(db=self.db, username=username, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        await UserCacheInvalidator.cache_user(db_user)
        return UserRead.model_validate(db_user)

    async def by_email(self, email: str) -> UserRead:
        """Cached lookup by email, raises when missing (the raise-semantic
        half of the old ``UserService.get_by_email``; enumeration-safe
        callers catch ``ModelNotFoundError``)."""
        cached = await UserCacheInvalidator.get_cached_user(_user_email_cache_key(email))
        if cached:
            logger.debug("Cache hit for user email", email=email)
            return UserRead.model_validate(cached)

        db_user = await crud_users.get(db=self.db, email=email, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", email)

        await UserCacheInvalidator.cache_user(db_user)
        return UserRead.model_validate(db_user)

    async def profile(self, identifier: str, *, tenant_id: Any) -> UserRead:
        """Load by ID-or-username and enforce tenant isolation (moved from
        ``UserService.get_profile``) — cross-tenant reads raise
        ``ModelNotFoundError`` (404, not 403)."""
        try:
            user = await self.by_id(int(identifier))
        except (ValueError, TypeError):
            try:
                user = await self.by_username(identifier)
            except ModelNotFoundError:
                user = None
        if user is None:
            raise ModelNotFoundError("User", identifier)
        if tenant_id is not None and str(user.tenant_id) != str(tenant_id):
            raise ModelNotFoundError("User", identifier)
        return user


def get_user_directory(db: AsyncSession) -> UserDirectory:
    """Factory for the narrow user-read Interface (facade parity with ``get_user_service``)."""
    return UserDirectory(db)
