"""RBAC Role Service - Business logic for role management.

Performance Optimization (R2-HYBRID MED-2):
- L1 in-memory cache (TTLCache) reduces Redis calls for repeated permission checks
- L2 Redis cache (TTL configurable) reduces DB queries
- Critical for endpoints with multiple permission checks per request
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

import structlog
from cachetools import TTLCache
from fastapi import Depends
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.monitoring.r1_unified_metrics import record_rbac_cache_hit, record_rbac_cache_miss
from src.app.core.rbac.dependencies import (
    invalidate_user_permissions_cache,
    invalidate_users_permissions_cache_batch,
)
from src.app.core.tenancy.context import TenantContext, get_current_tenant
from src.app.modules._base.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import RoleCreate, RoleUpdate
from src.app.modules.users.session_invalidation import InvalidationReason

if TYPE_CHECKING:
    from redis.asyncio import Redis

import asyncio

logger = structlog.get_logger(__name__)
# L1 In-Memory Cache for RBAC permissions (R2-HYBRID MED-2)
# - maxsize: 500 role-permission sets (covers most deployments)
# - ttl: 30 seconds (balance between freshness and performance)
# - Thread-safe: Access protected by _L1_RBAC_CACHE_LOCK to prevent dictionary iteration bugs
# - Key format: "{tenant_id}:{role_id}" -> list of permission dicts
_L1_RBAC_CACHE: TTLCache[str, list[dict[str, Any]]] = TTLCache(maxsize=500, ttl=30.0)
# BUG FIX: asyncio.Lock() must NOT be created at module import time because that
# binds it to whichever event loop is current at that moment (or raises a
# DeprecationWarning / RuntimeError in Python ≥ 3.10 if there is no running loop).
# Instead we use a None sentinel and create the lock lazily on first use inside
# an async context so it is always bound to the correct running event loop.
_L1_RBAC_CACHE_LOCK: asyncio.Lock | None = None


def _get_rbac_cache_lock() -> asyncio.Lock:
    """Return the module-level RBAC cache lock, creating it lazily if needed."""
    global _L1_RBAC_CACHE_LOCK
    if _L1_RBAC_CACHE_LOCK is None:
        _L1_RBAC_CACHE_LOCK = asyncio.Lock()
    return _L1_RBAC_CACHE_LOCK


class RoleService:
    """Service for managing roles within a tenant."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        redis_client: "Redis | None" = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._redis_client = redis_client
        self._cache_prefix = "rbac:perms"

    async def get_roles(self, skip: int = 0, limit: int = 100) -> Sequence[Role]:
        """Get all roles for the current tenant."""
        stmt = select(Role).where(Role.tenant_id == self.tenant_id).offset(skip).limit(limit).order_by(Role.id)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_role(self, role_id: int) -> Role:
        """Get a role by ID within the current tenant."""
        stmt = select(Role).where(and_(Role.id == role_id, Role.tenant_id == self.tenant_id))
        result = await self.db.execute(stmt)
        role = result.scalar_one_or_none()
        if not role:
            raise ModelNotFoundError("Role", role_id)
        return role

    async def get_role_by_name(self, name: str) -> Role | None:
        """Get a role by name within the current tenant."""
        stmt = select(Role).where(and_(Role.name == name, Role.tenant_id == self.tenant_id))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_role(self, role_in: RoleCreate) -> Role:
        """Create a new custom role for the current tenant."""
        # Check for duplicate name
        existing = await self.get_role_by_name(role_in.name)
        if existing:
            raise DuplicateError("name", role_in.name)

        role = Role(
            tenant_id=self.tenant_id,
            name=role_in.name,
            description=role_in.description,
            is_system=False,  # Custom roles are never system roles
        )
        self.db.add(role)
        try:
            await self.db.commit()
            await self.db.refresh(role)
        except IntegrityError:
            await self.db.rollback()
            raise DuplicateError("name", role_in.name)
        return role

    async def update_role(self, role_id: int, role_in: RoleUpdate) -> Role:
        """Update an existing role. System roles cannot be modified."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System roles cannot be modified", reason="system_role")

        # Check for duplicate name if name is being changed
        if role_in.name is not None and role_in.name != role.name:
            existing = await self.get_role_by_name(role_in.name)
            if existing:
                raise DuplicateError("name", role_in.name)
            role.name = role_in.name
        if role_in.description is not None:
            role.description = role_in.description

        try:
            await self.db.commit()
            await self.db.refresh(role)
        except IntegrityError:
            await self.db.rollback()
            raise DuplicateError("name", role.name)
        return role

    async def delete_role(self, role_id: int) -> None:
        """Delete a role. System roles cannot be deleted."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System roles cannot be deleted", reason="system_role")

        # BUG FIX: Find all users that have this role BEFORE deleting,
        # so we can invalidate their permission caches after deletion.
        affected_users_stmt = select(UserRole.user_id).where(
            and_(
                UserRole.role_id == role_id,
                UserRole.tenant_id == self.tenant_id,
            )
        )
        affected_users_result = await self.db.execute(affected_users_stmt)
        affected_user_ids = list(affected_users_result.scalars().all())

        # FIX: Delete relations before deleting the role to prevent IntegrityError
        from sqlalchemy import delete

        # Delete related role permissions
        await self.db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))

        # Delete related user roles
        await self.db.execute(delete(UserRole).where(UserRole.role_id == role_id))

        await self.db.delete(role)
        # T110: Defer commit until after token_version increment to maintain atomicity
        # Invalidate cache for the deleted role
        await self._invalidate_role_cache(role_id)

        # BUG FIX: Also invalidate permission caches and bump token_version for all affected users
        users_records: list[Any] = []
        if affected_user_ids:
            from sqlalchemy import update as sa_update

            from src.app.modules.users.models import User

            # Project rows BEFORE the bump so the batch can write old+1 == new DB value.
            users_stmt = select(
                User.id, User.email, User.username, User.tenant_id, User.token_version
            ).where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
            users_result = await self.db.execute(users_stmt)
            users_records = list(users_result.all())

            await self.db.execute(
                sa_update(User)
                .where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
                .values(token_version=User.token_version + 1)
            )

        # Commit both the role deletion and the token version updates atomically
        await self.db.commit()

        for user_id in affected_user_ids:
            await invalidate_user_permissions_cache(user_id, self.tenant_id)

        # Sync token_version cache (bump=True → old+1 == new DB value; CREDENTIAL_CHANGE per policy).
        if users_records:
            from src.app.modules.users.service import UserService
            from src.app.modules.users.session_invalidation import InvalidationReason

            await UserService(self.db)._invalidate_user_cache_batch(
                users_records, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
            )

    async def get_all_permissions(self) -> Sequence[Permission]:
        """Get all available permissions in the system.

        Returns all permissions that can be assigned to roles.
        This is a system-wide query and not tenant-specific.

        Returns:
            Sequence of all Permission objects
        """
        stmt = select(Permission).order_by(Permission.slug)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_role_permissions(self, role_id: int) -> Sequence[Permission]:
        """Get all permissions for a role with L1+L2 caching.

        Cache hierarchy (R2-HYBRID MED-2):
        - L1: In-memory TTLCache (5s TTL) - eliminates Redis RTT
        - L2: Redis cache (configurable TTL) - eliminates DB queries

        Args:
            role_id: Role ID to get permissions for

        Returns:
            Sequence of Permission objects

        Performance:
            - L1 cache hit: ~0.01-0.05ms (in-process)
            - L2 cache hit: ~0.5-1ms (Redis RTT)
            - Cache miss: ~2-5ms (DB query + cache writes)
        """
        l1_cache_key = f"{self.tenant_id}:{role_id}"

        # L1: Check in-memory cache first (fastest path)
        try:
            async with _get_rbac_cache_lock():
                cached_perms = _L1_RBAC_CACHE.get(l1_cache_key)
            if cached_perms is not None:
                # L1 hit - reconstruct Permission objects
                permissions = self._deserialize_permissions(cached_perms)
                record_rbac_cache_hit()
                logger.debug(
                    "RBAC L1 cache HIT",
                    role_id=role_id,
                    tenant_id=self.tenant_id,
                    permission_count=len(permissions),
                )
                return permissions
        except Exception as e:
            logger.warning("RBAC L1 cache read error: %s", e)

        # L2: Check Redis cache (if enabled)
        if settings.RBAC_USE_REDIS_CACHE and self._redis_client:
            cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
            try:
                cached = await self._redis_client.get(cache_key)
                if cached:
                    # Deserialize and populate L1 cache
                    perm_dicts = json_loads(cached)
                    permissions = self._deserialize_permissions(perm_dicts)

                    # Populate L1 cache
                    try:
                        async with _get_rbac_cache_lock():
                            _L1_RBAC_CACHE[l1_cache_key] = perm_dicts
                    except Exception as e:
                        logger.warning("RBAC L1 cache write error: %s", e)

                    record_rbac_cache_hit()
                    logger.debug(
                        "RBAC L2 cache HIT (populated L1)",
                        role_id=role_id,
                        tenant_id=self.tenant_id,
                        permission_count=len(permissions),
                    )
                    return permissions
            except Exception as e:
                logger.warning(
                    "RBAC L2 cache read error, falling back to DB",
                    error=str(e),
                    role_id=role_id,
                )

        # L3: Cache miss - query database
        record_rbac_cache_miss()
        await self.get_role(role_id)  # Verify role exists and belongs to tenant

        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        result = await self.db.execute(stmt)
        permissions = list(result.scalars().all())

        # Serialize for caching
        perm_dicts = [
            {
                "id": p.id,
                "slug": p.slug,
                "description": p.description,
            }
            for p in permissions
        ]

        # Only cache non-empty permission sets.
        # Empty lists may indicate a role has no permissions yet; caching them
        # risks propagating a stale empty list if permissions are added shortly after.
        if perm_dicts:
            try:
                async with _get_rbac_cache_lock():
                    _L1_RBAC_CACHE[l1_cache_key] = perm_dicts
            except Exception as e:
                logger.warning("RBAC L1 cache write error: %s", e)

            # Populate L2 Redis cache (if enabled)
            if settings.RBAC_USE_REDIS_CACHE and self._redis_client:
                cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
                try:
                    await self._redis_client.setex(
                        cache_key,
                        settings.RBAC_CACHE_TTL_SECONDS,
                        json_dumps(perm_dicts, as_str=True),
                    )
                    logger.debug(
                        "RBAC L2 cache SET",
                        role_id=role_id,
                        tenant_id=self.tenant_id,
                        permission_count=len(permissions),
                        ttl=settings.RBAC_CACHE_TTL_SECONDS,
                    )
                except Exception as e:
                    logger.warning(
                        "RBAC L2 cache write error",
                        error=str(e),
                        role_id=role_id,
                    )

        return permissions

    def _deserialize_permissions(self, perm_dicts: list[dict[str, Any]]) -> list[Permission]:
        """Deserialize permission dicts to Permission objects.

        Creates minimal Permission objects from cached data without
        touching the database. These are transient objects suitable
        for permission checking.

        C1 FIX: ``Permission.id`` is ``init=False`` (SQLAlchemy 2.0 typed
        dataclass style), so it cannot be passed to the constructor. The prior
        ``perm.__dict__["id"] = ...`` write bypassed instrumentation exactly
        like the ``object.__setattr__`` it claimed to replace — its comment was
        misleading. ``set_committed_value`` is the canonical SQLAlchemy API for
        populating a column on a detached instance without marking it dirty or
        triggering session side-effects.
        """
        from sqlalchemy.orm.attributes import set_committed_value

        permissions = []
        for p in perm_dicts:
            perm = Permission(
                slug=p["slug"],
                description=p.get("description"),
            )
            set_committed_value(perm, "id", p["id"])
            permissions.append(perm)
        return permissions

    async def _invalidate_role_cache(self, role_id: int) -> None:
        """Invalidate cache for a specific role.

        Called when role permissions are modified.
        Clears both L1 (memory) and L2 (Redis) caches.
        """
        l1_cache_key = f"{self.tenant_id}:{role_id}"

        # Clear L1 cache first (always)
        try:
            async with _get_rbac_cache_lock():
                _L1_RBAC_CACHE.pop(l1_cache_key, None)
            logger.debug(
                "RBAC L1 cache INVALIDATED",
                role_id=role_id,
                tenant_id=self.tenant_id,
            )
        except Exception as e:
            logger.warning("RBAC L1 cache invalidation error: %s", e)

        # Clear L2 Redis cache
        if not self._redis_client:
            return

        cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
        try:
            await self._redis_client.delete(cache_key)
            logger.info(
                "RBAC L2 cache INVALIDATED",
                role_id=role_id,
                tenant_id=self.tenant_id,
            )
        except Exception as e:
            logger.warning(
                "RBAC L2 cache invalidation error",
                error=str(e),
                role_id=role_id,
            )

    async def _invalidate_all_role_caches(self) -> None:
        """Invalidate all role caches for this tenant.

        Called when a permission is modified (affects all roles).
        Clears both L1 (memory) and L2 (Redis) caches.
        Uses SCAN instead of KEYS for non-blocking operation.
        """
        # Clear all L1 cache entries for this tenant
        try:
            tenant_prefix = f"{self.tenant_id}:"
            async with _get_rbac_cache_lock():
                keys_to_remove = [k for k in _L1_RBAC_CACHE.keys() if k.startswith(tenant_prefix)]
                for key in keys_to_remove:
                    _L1_RBAC_CACHE.pop(key, None)
            logger.debug(
                "RBAC L1 cache INVALIDATED ALL",
                tenant_id=self.tenant_id,
                deleted_keys=len(keys_to_remove),
            )
        except Exception as e:
            logger.warning("RBAC L1 cache invalidation error: %s", e)

        # Clear L2 Redis cache
        if not self._redis_client:
            return

        try:
            pattern = f"{self._cache_prefix}:{self.tenant_id}:*"
            cursor = 0
            deleted = 0

            while True:
                cursor, keys = await self._redis_client.scan(cursor, match=pattern, count=100)
                if keys:
                    await self._redis_client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            logger.info(
                "RBAC L2 cache INVALIDATED ALL",
                tenant_id=self.tenant_id,
                deleted_keys=deleted,
            )
        except Exception as e:
            logger.warning(
                "RBAC L2 cache invalidation error",
                error=str(e),
                tenant_id=self.tenant_id,
            )

    async def add_permission_to_role(self, role_id: int, permission_slug: str) -> None:
        """Add a permission to a role."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System role permissions cannot be modified", reason="system_role")

        # Get permission by slug
        stmt = select(Permission).where(Permission.slug == permission_slug)
        result = await self.db.execute(stmt)
        permission = result.scalar_one_or_none()
        if not permission:
            raise ModelNotFoundError("Permission", permission_slug)

        # Check if already assigned
        rp_stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission.id,
            )
        )
        result = await self.db.execute(rp_stmt)
        if result.scalar_one_or_none():
            return  # Already assigned, idempotent

        role_permission = RolePermission(
            role_id=role_id,
            permission_id=permission.id,
        )
        self.db.add(role_permission)

        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            return  # Already assigned concurrently, idempotent

        # Invalidate cache for this role
        await self._invalidate_role_cache(role_id)

        # Invalidate user permission caches and bump token_version for affected users
        affected_users_stmt = select(UserRole.user_id).where(
            and_(UserRole.role_id == role_id, UserRole.tenant_id == self.tenant_id)
        )
        affected_users_result = await self.db.execute(affected_users_stmt)
        affected_user_ids = list(affected_users_result.scalars().all())

        if affected_user_ids:
            from sqlalchemy import update as sa_update

            from src.app.modules.users.models import User

            # N3: project only the columns needed for cache invalidation
            # (id/email/username/tenant_id/token_version) instead of loading
            # full User rows (incl. hashed_password), then invalidate the whole
            # batch in ONE Redis pipeline instead of 2 round-trips per user.
            users_stmt = select(
                User.id, User.email, User.username, User.tenant_id, User.token_version
            ).where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
            users_result = await self.db.execute(users_stmt)
            users_records = users_result.all()

            await self.db.execute(
                sa_update(User)
                .where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
                .values(token_version=User.token_version + 1)
            )
            await self.db.commit()

            from src.app.modules.users.service import UserService
            from src.app.modules.users.session_invalidation import InvalidationReason

            await UserService(self.db)._invalidate_user_cache_batch(
                users_records, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
            )
            await invalidate_users_permissions_cache_batch(affected_user_ids, self.tenant_id)

    async def remove_permission_from_role(self, role_id: int, permission_slug: str) -> None:
        """Remove a permission from a role."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System role permissions cannot be modified", reason="system_role")

        # Get permission by slug
        stmt = select(Permission).where(Permission.slug == permission_slug)
        result = await self.db.execute(stmt)
        permission = result.scalar_one_or_none()
        if not permission:
            raise ModelNotFoundError("Permission", permission_slug)

        # Find and delete the role permission
        rp_stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission.id,
            )
        )
        result = await self.db.execute(rp_stmt)
        role_permission = result.scalar_one_or_none()
        if role_permission:
            await self.db.delete(role_permission)
            await self.db.commit()

            # Invalidate cache for this role
            await self._invalidate_role_cache(role_id)

            # Invalidate user permission caches and bump token_version for affected users
            affected_users_stmt = select(UserRole.user_id).where(
                and_(UserRole.role_id == role_id, UserRole.tenant_id == self.tenant_id)
            )
            affected_users_result = await self.db.execute(affected_users_stmt)
            affected_user_ids = list(affected_users_result.scalars().all())

            if affected_user_ids:
                from sqlalchemy import update as sa_update

                from src.app.modules.users.models import User

                # N3: project only the columns needed for cache invalidation
                # (id/email/username/tenant_id/token_version) instead of loading
                # full User rows (incl. hashed_password), then invalidate the whole
                # batch in ONE Redis pipeline instead of 2 round-trips per user.
                users_stmt = select(
                    User.id, User.email, User.username, User.tenant_id, User.token_version
                ).where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
                users_result = await self.db.execute(users_stmt)
                users_records = users_result.all()

                await self.db.execute(
                    sa_update(User)
                    .where(and_(User.id.in_(affected_user_ids), User.tenant_id == self.tenant_id))
                    .values(token_version=User.token_version + 1)
                )
                await self.db.commit()

                from src.app.modules.users.service import UserService
                from src.app.modules.users.session_invalidation import InvalidationReason

                await UserService(self.db)._invalidate_user_cache_batch(
                    users_records, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
                )

            if affected_user_ids:
                await invalidate_users_permissions_cache_batch(affected_user_ids, self.tenant_id)

    async def assign_role_to_user(self, user_id: int, role_id: int) -> None:
        """Assign a role to a user within the current tenant.

        Security: Validates both role AND user belong to the current tenant.
        """
        await self.get_role(role_id)  # Verify role exists and belongs to tenant

        # Verify user belongs to the same tenant (P1 fix: tenant isolation)
        from src.app.modules.users.models import User

        user_stmt = select(User).where(and_(User.id == user_id, User.tenant_id == self.tenant_id))
        user_result = await self.db.execute(user_stmt)
        user_record = user_result.scalar_one_or_none()
        if not user_record:
            raise ModelNotFoundError("User", user_id)

        # Check if already assigned
        stmt = select(UserRole).where(
            and_(
                UserRole.user_id == user_id,
                UserRole.tenant_id == self.tenant_id,
                UserRole.role_id == role_id,
            )
        )
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            return  # Already assigned, idempotent

        user_role = UserRole(
            user_id=user_id,
            tenant_id=self.tenant_id,
            role_id=role_id,
        )
        self.db.add(user_role)

        # SECURITY FIX: Increment token_version to invalidate active JWTs on role change
        from sqlalchemy import update as sa_update

        from src.app.modules.users.models import User

        await self.db.execute(sa_update(User).where(User.id == user_id).values(token_version=User.token_version + 1))

        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            return  # Already assigned concurrently, idempotent

        # Invalidate user cache and user permissions cache
        # Role grant changes the user's effective permission set, which
        # CREDENTIAL_CHANGE in spirit (JWT-embedded roles / permission
        # bitmask become stale). Bump token_version so cached permissions
        # are recomputed on the next request.
        from src.app.modules.users.service import UserService

        await UserService(self.db)._invalidate_user_cache(
            user_record, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
        )
        await invalidate_user_permissions_cache(user_id, self.tenant_id)

    async def remove_role_from_user(self, user_id: int, role_id: int) -> None:
        """Remove a role from a user within the current tenant.

        Security: Validates user belongs to the current tenant.
        """
        # Verify user belongs to the same tenant (P1 fix: tenant isolation)
        from src.app.modules.users.models import User

        user_stmt = select(User).where(and_(User.id == user_id, User.tenant_id == self.tenant_id))
        user_result = await self.db.execute(user_stmt)
        user_record = user_result.scalar_one_or_none()
        if not user_record:
            raise ModelNotFoundError("User", user_id)

        stmt = select(UserRole).where(
            and_(
                UserRole.user_id == user_id,
                UserRole.tenant_id == self.tenant_id,
                UserRole.role_id == role_id,
            )
        )
        result = await self.db.execute(stmt)
        user_role = result.scalar_one_or_none()
        if user_role:
            await self.db.delete(user_role)

            # SECURITY FIX: Increment token_version atomicaly to invalidate active JWTs on role change
            from sqlalchemy import update as sa_update

            from src.app.modules.users.models import User

            await self.db.execute(
                sa_update(User).where(User.id == user_id).values(token_version=User.token_version + 1)
            )
            await self.db.commit()
            # Role removal changes the user's effective permission set, so
            # bump token_version (CREDENTIAL_CHANGE) to invalidate sessions
            # that were issued under the old role grant.
            from src.app.modules.users.service import UserService

            await UserService(self.db)._invalidate_user_cache(
                user_record, bump_token_version=True, reason=InvalidationReason.CREDENTIAL_CHANGE
            )
            await invalidate_user_permissions_cache(user_id, self.tenant_id)
    async def get_user_roles(self, user_id: int) -> Sequence[Role]:
        """Get all roles for a user within the current tenant."""
        stmt = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                and_(
                    UserRole.user_id == user_id,
                    UserRole.tenant_id == self.tenant_id,
                )
            )
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()


def get_role_service(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> RoleService:
    """Dependency injection factory for RoleService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db
    tenant : TenantContext
        Current tenant context from get_current_tenant

    Returns
    -------
    RoleService
        Service instance with database session and tenant context
    """
    return RoleService(db=db, tenant_id=tenant.id)