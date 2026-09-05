"""RBAC database operations and CRUD repositories.

Extracted per PR-07 (Finding #6) to separate persistence from business policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastcrud import FastCRUD
from sqlalchemy import and_, delete, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.db.tenant_crud import TenantAwareCRUD, scoped_query, tenant_scope
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import (
    PermissionBase,
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)

logger = get_logger(__name__)


class CRUDRole(TenantAwareCRUD[Role, RoleCreate, RoleUpdate, RoleUpdate, Any, RoleRead]):
    """Tenant-aware repository for Role entities."""

    async def get_roles(self, db: AsyncSession, tenant_id: UUID, skip: int = 0, limit: int = 100) -> Sequence[Role]:
        """Get paginated roles for a tenant."""
        stmt = scoped_query(Role, tenant_id).offset(skip).limit(limit).order_by(Role.id)
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_name(self, db: AsyncSession, tenant_id: UUID, name: str) -> Role | None:
        """Get a role by name within a tenant."""
        stmt = scoped_query(Role, tenant_id).where(Role.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, db: AsyncSession, tenant_id: UUID, role_id: int) -> Role | None:
        """Get a role by ID within a tenant."""
        stmt = scoped_query(Role, tenant_id).where(Role.id == role_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_relations(self, db: AsyncSession, role_id: int, tenant_id: UUID | None = None) -> None:
        """Delete RolePermission and UserRole relations associated with a role."""
        if tenant_id is not None:
            # RolePermission has no tenant_id: scope via parent Role IN subquery.
            role_ids_stmt = select(Role.id).where(Role.id == role_id, tenant_scope(Role, tenant_id))
            await db.execute(delete(RolePermission).where(RolePermission.role_id.in_(role_ids_stmt)))
            await db.execute(delete(UserRole).where(UserRole.role_id == role_id, tenant_scope(UserRole, tenant_id)))
        else:
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
            await db.execute(delete(UserRole).where(UserRole.role_id == role_id))

    async def get_user_roles(self, db: AsyncSession, tenant_id: UUID, user_id: int) -> Sequence[Role]:
        """Get all roles assigned to a user within a tenant."""
        stmt = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .where(tenant_scope(UserRole, tenant_id))
        )
        result = await db.execute(stmt)
        return result.scalars().all()


class CRUDPermission(FastCRUD[Permission, PermissionBase, PermissionBase, PermissionBase, Any, PermissionRead]):
    """Repository for global system Permission entities."""

    async def get_all(self, db: AsyncSession) -> Sequence[Permission]:
        """Get all available permissions ordered by slug."""
        stmt = select(Permission).order_by(Permission.slug)
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_slug(self, db: AsyncSession, slug: str) -> Permission | None:
        """Get permission by slug."""
        stmt = select(Permission).where(Permission.slug == slug)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_role(self, db: AsyncSession, role_id: int) -> Sequence[Permission]:
        """Get all permissions assigned to a role."""
        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


def _rows_affected(result: object) -> bool:
    """Return True when a write statement affected at least one row.

    Real drivers report an int ``rowcount``; anything else (test doubles,
    drivers that report None) is treated as applied so callers stay total.
    """
    rowcount = getattr(result, "rowcount", None)
    if isinstance(rowcount, bool) or not isinstance(rowcount, int):
        return True
    return rowcount > 0


class CRUDAssociations:
    """Repository for UserRole and RolePermission associations."""

    @staticmethod
    async def get_role_affected_user_ids(db: AsyncSession, tenant_id: UUID, role_id: int) -> list[int]:
        """Get all user IDs assigned to a role within a tenant."""
        stmt = select(UserRole.user_id).where(UserRole.role_id == role_id).where(tenant_scope(UserRole, tenant_id))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_role_permission(db: AsyncSession, role_id: int, permission_id: int) -> RolePermission | None:
        """Check if a permission is assigned to a role."""
        stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission_id,
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def remove_role_permission(db: AsyncSession, role_id: int, permission_id: int) -> bool:
        """Remove a permission from a role (single-statement, race-safe)."""
        result = await db.execute(
            delete(RolePermission).where(
                and_(
                    RolePermission.role_id == role_id,
                    RolePermission.permission_id == permission_id,
                )
            )
        )
        return _rows_affected(result)

    @staticmethod
    async def get_user_role(db: AsyncSession, tenant_id: UUID, user_id: int, role_id: int) -> UserRole | None:
        """Get user role assignment within a tenant."""
        stmt = scoped_query(UserRole, tenant_id).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def remove_user_role(db: AsyncSession, tenant_id: UUID, user_id: int, role_id: int) -> bool:
        """Remove user role assignment (single-statement, race-safe)."""
        result = await db.execute(
            delete(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role_id,
                tenant_scope(UserRole, tenant_id),
            )
        )
        return _rows_affected(result)

    @staticmethod
    async def bump_user_token_version(db: AsyncSession, user_id: int) -> None:
        """Increment token_version for user in database."""
        from src.app.modules.users.models import User

        await db.execute(sa_update(User).where(User.id == user_id).values(token_version=User.token_version + 1))


crud_roles = CRUDRole(Role)
crud_permissions = CRUDPermission(Permission)
crud_associations = CRUDAssociations()

__all__ = [
    "CRUDAssociations",
    "CRUDPermission",
    "CRUDRole",
    "crud_associations",
    "crud_permissions",
    "crud_roles",
]
