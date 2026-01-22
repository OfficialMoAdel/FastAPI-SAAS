"""RBAC Role Service - Business logic for role management."""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant
from ...core.config import settings
from ...core.db.database import async_get_db
from ...core.json_utils import json_dumps, json_loads
from ...core.monitoring.r1_unified_metrics import record_rbac_cache_hit, record_rbac_cache_miss
from ...core.rbac.dependencies import invalidate_user_permissions_cache
from ...modules.tenants.models import Tenant
from .._base.exceptions import DuplicateError, ForbiddenError, ModelNotFoundError
from .models import Permission, Role, RolePermission, UserRole
from .schemas import RoleCreate, RoleUpdate

if TYPE_CHECKING:
    from redis.asyncio import Redis

import structlog

logger = structlog.get_logger(__name__)


class RoleService:
    """Service for managing roles within a tenant."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: int,
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
        await self.db.commit()
        await self.db.refresh(role)
        return role

    async def update_role(self, role_id: int, role_in: RoleUpdate) -> Role:
        """Update an existing role. System roles cannot be modified."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System roles cannot be modified", reason="system_role")

        # Check for duplicate name if name is being changed
        if role_in.name != role.name:
            existing = await self.get_role_by_name(role_in.name)
            if existing:
                raise DuplicateError("name", role_in.name)

        role.name = role_in.name
        if role_in.description is not None:
            role.description = role_in.description

        await self.db.commit()
        await self.db.refresh(role)
        return role

    async def delete_role(self, role_id: int) -> None:
        """Delete a role. System roles cannot be deleted."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise ForbiddenError("System roles cannot be deleted", reason="system_role")

        await self.db.delete(role)
        await self.db.commit()

    async def get_role_permissions(self, role_id: int) -> Sequence[Permission]:
        """Get all permissions for a role with Redis caching.

        Uses Redis cache when R1_RBAC_USE_REDIS_CACHE=true to avoid
        database queries on every permission check.

        Args:
            role_id: Role ID to get permissions for

        Returns:
            Sequence of Permission objects

        Performance:
            - Cache hit: ~0.05-0.1ms
            - Cache miss: ~0.5-2ms (DB query + cache write)
        """
        # Check cache first (if enabled)
        if settings.r1_unified.RBAC_USE_REDIS_CACHE and self._redis_client:
            cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
            try:
                cached = await self._redis_client.get(cache_key)
                if cached:
                    # Deserialize full Permission objects
                    perm_dicts = json_loads(cached)
                    permissions = [
                        Permission(
                            id=p["id"],
                            slug=p["slug"],
                            description=p.get("description"),
                            created_at=datetime.fromisoformat(p["created_at"]),
                            updated_at=datetime.fromisoformat(p["updated_at"]),
                        )
                        for p in perm_dicts
                    ]
                    record_rbac_cache_hit()
                    logger.debug(
                        "RBAC cache HIT",
                        role_id=role_id,
                        tenant_id=self.tenant_id,
                        permission_count=len(permissions),
                    )
                    return permissions
            except Exception as e:
                logger.warning(
                    "RBAC cache read error, falling back to DB",
                    error=str(e),
                    role_id=role_id,
                )

        # Cache miss - query database
        record_rbac_cache_miss()
        await self.get_role(role_id)  # Verify role exists and belongs to tenant

        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        result = await self.db.execute(stmt)
        permissions = list(result.scalars().all())

        # Cache the results (if enabled)
        if settings.r1_unified.RBAC_USE_REDIS_CACHE and self._redis_client and permissions:
            cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
            try:
                perm_dicts = [
                    {
                        "id": p.id,
                        "slug": p.slug,
                        "description": p.description,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    }
                    for p in permissions
                ]
                await self._redis_client.setex(
                    cache_key,
                    settings.r1_unified.RBAC_CACHE_TTL_SECONDS,
                    json_dumps(perm_dicts, as_str=True),
                )
                logger.debug(
                    "RBAC cache SET",
                    role_id=role_id,
                    tenant_id=self.tenant_id,
                    permission_count=len(permissions),
                    ttl=settings.r1_unified.RBAC_CACHE_TTL_SECONDS,
                )
            except Exception as e:
                logger.warning(
                    "RBAC cache write error",
                    error=str(e),
                    role_id=role_id,
                )

        return permissions

    async def _invalidate_role_cache(self, role_id: int) -> None:
        """Invalidate cache for a specific role.

        Called when role permissions are modified.
        """
        if not self._redis_client:
            return

        cache_key = f"{self._cache_prefix}:{self.tenant_id}:{role_id}"
        try:
            await self._redis_client.delete(cache_key)
            logger.info(
                "RBAC cache INVALIDATED",
                role_id=role_id,
                tenant_id=self.tenant_id,
            )
        except Exception as e:
            logger.warning(
                "RBAC cache invalidation error",
                error=str(e),
                role_id=role_id,
            )

    async def _invalidate_all_role_caches(self) -> None:
        """Invalidate all role caches for this tenant.

        Called when a permission is modified (affects all roles).
        Uses SCAN instead of KEYS for non-blocking operation.
        """
        if not self._redis_client:
            return

        try:
            pattern = f"{self._cache_prefix}:{self.tenant_id}:*"
            cursor = 0
            deleted = 0

            while True:
                cursor, keys = await self._redis_client.scan(
                    cursor, match=pattern, count=100
                )
                if keys:
                    await self._redis_client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            logger.info(
                "RBAC cache INVALIDATED ALL",
                tenant_id=self.tenant_id,
                deleted_keys=deleted,
            )
        except Exception as e:
            logger.warning(
                "RBAC cache invalidation error",
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
        stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission.id,
            )
        )
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            return  # Already assigned, idempotent

        role_permission = RolePermission(
            role_id=role_id,
            permission_id=permission.id,
        )
        self.db.add(role_permission)
        await self.db.commit()

        # Invalidate cache for this role
        await self._invalidate_role_cache(role_id)

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
        stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission.id,
            )
        )
        result = await self.db.execute(stmt)
        role_permission = result.scalar_one_or_none()
        if role_permission:
            await self.db.delete(role_permission)
            await self.db.commit()

            # Invalidate cache for this role
            await self._invalidate_role_cache(role_id)

    async def assign_role_to_user(self, user_id: int, role_id: int) -> None:
        """Assign a role to a user within the current tenant."""
        await self.get_role(role_id)  # Verify role exists and belongs to tenant

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
        await self.db.commit()

        # Invalidate user permissions cache
        await invalidate_user_permissions_cache(user_id, self.tenant_id)

    async def remove_role_from_user(self, user_id: int, role_id: int) -> None:
        """Remove a role from a user within the current tenant."""
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
            await self.db.commit()

            # Invalidate user permissions cache
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
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> RoleService:
    """Dependency injection factory for RoleService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db
    tenant : Tenant
        Current tenant from get_current_tenant

    Returns
    -------
    RoleService
        Service instance with database session and tenant context
    """
    return RoleService(db=db, tenant_id=tenant.id)
