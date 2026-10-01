"""RBAC Router - Role management API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.tenancy.context import TenantContext, get_current_tenant
from src.app.modules.rbac.schemas import PermissionRead, RoleCreate, RoleRead, RoleUpdate
from src.app.modules.rbac.service import RoleService

router = APIRouter(prefix="/roles", tags=["rbac"])


def get_role_service(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> RoleService:
    """Dependency injection factory for RoleService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    return RoleService(db=db, tenant_id=tenant.id)


@router.get(
    "",
    response_model=list[RoleRead],
    dependencies=[Depends(requires_permission(Permission.ROLES_READ))],
)
async def list_roles(
    service: Annotated[RoleService, Depends(get_role_service)],
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """List all roles for the current tenant."""
    roles = await service.get_roles(skip=skip, limit=limit)
    return roles


@router.get(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(requires_permission(Permission.ROLES_READ))],
)
async def get_role(
    role_id: int,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> Any:
    """Get a specific role by ID."""
    role = await service.get_role(role_id)
    return role


@router.post(
    "",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))],
)
async def create_role(
    role_in: RoleCreate,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> Any:
    """Create a new custom role."""
    role = await service.create_role(role_in)
    return role


@router.put(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))],
)
async def update_role(
    role_id: int,
    role_in: RoleUpdate,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> Any:
    """Update an existing role. System roles cannot be modified."""
    role = await service.update_role(role_id, role_in)
    return role


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))],
)
async def delete_role(
    role_id: int,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> None:
    """Delete a role. System roles cannot be deleted."""
    await service.delete_role(role_id)


# Permission management for roles
@router.get(
    "/{role_id}/permissions",
    response_model=list[PermissionRead],
    dependencies=[Depends(requires_permission(Permission.ROLES_READ))],
)
async def get_role_permissions(
    role_id: int,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> Any:
    """Get all permissions assigned to a role."""
    permissions = await service.get_role_permissions(role_id)
    return permissions


@router.post(
    "/{role_id}/permissions/{permission_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))],
)
async def add_permission_to_role(
    role_id: int,
    permission_slug: str,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> None:
    """Add a permission to a role. Idempotent operation."""
    await service.add_permission_to_role(role_id, permission_slug)


@router.delete(
    "/{role_id}/permissions/{permission_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))],
)
async def remove_permission_from_role(
    role_id: int,
    permission_slug: str,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> None:
    """Remove a permission from a role."""
    await service.remove_permission_from_role(role_id, permission_slug)


# User role assignment
@router.post(
    "/{role_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.ROLES_ASSIGN))],
)
async def assign_role_to_user(
    role_id: int,
    user_id: int,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> None:
    """Assign a role to a user. Idempotent operation."""
    await service.assign_role_to_user(user_id, role_id)


@router.delete(
    "/{role_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.ROLES_ASSIGN))],
)
async def remove_role_from_user(
    role_id: int,
    user_id: int,
    service: Annotated[RoleService, Depends(get_role_service)],
) -> None:
    """Remove a role from a user."""
    await service.remove_role_from_user(user_id, role_id)
