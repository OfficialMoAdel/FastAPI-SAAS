"""
Permissions Router

Provides endpoints for listing all available permissions in the system.
This is separate from the roles router to match the old backend's API structure.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.rbac.router import get_role_service
from src.app.modules.rbac.schemas import PermissionRead
from src.app.modules.rbac.service import RoleService

router = APIRouter(prefix="/permissions", tags=["rbac"])


@router.get("", response_model=list[PermissionRead], dependencies=[Depends(requires_permission(Permission.ROLES_READ))])
async def list_permissions(
    service: Annotated[RoleService, Depends(get_role_service)],
):
    """List all available permissions in the system.

    Returns all permissions that can be assigned to roles.
    This is useful for building role management UIs where admins
    can select which permissions to grant to a role.

    **Requires:** Authentication (any authenticated user can list permissions)
    """
    permissions = await service.get_all_permissions()
    return permissions  # type: ignore[return-value, no-any-return]
