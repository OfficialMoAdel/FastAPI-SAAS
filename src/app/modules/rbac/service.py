"""RBAC Role Service - Business logic for role management."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant
from ...core.db.database import async_get_db
from ...core.rbac.dependencies import invalidate_user_permissions_cache
from ...modules.tenants.models import Tenant
from .._base.exceptions import DuplicateError, ModelNotFoundError
from .models import Permission, Role, RolePermission, UserRole
from .schemas import RoleCreate, RoleUpdate


class RoleService:
    """Service for managing roles within a tenant."""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id

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
            raise DuplicateError("is_system", "System roles cannot be modified")

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
            raise DuplicateError("is_system", "System roles cannot be deleted")

        await self.db.delete(role)
        await self.db.commit()

    async def get_role_permissions(self, role_id: int) -> Sequence[Permission]:
        """Get all permissions for a role."""
        await self.get_role(role_id)  # Verify role exists and belongs to tenant

        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def add_permission_to_role(self, role_id: int, permission_slug: str) -> None:
        """Add a permission to a role."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise DuplicateError("is_system", "System role permissions cannot be modified")

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

    async def remove_permission_from_role(self, role_id: int, permission_slug: str) -> None:
        """Remove a permission from a role."""
        role = await self.get_role(role_id)

        if role.is_system:
            raise DuplicateError("is_system", "System role permissions cannot be modified")

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
