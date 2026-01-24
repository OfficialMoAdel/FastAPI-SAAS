"""
Permissions Router

Provides endpoints for listing all available permissions in the system.
This is separate from the roles router to match the old backend's API structure.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from .schemas import PermissionOut
from .service import RoleService, get_role_service

router = APIRouter(prefix="/permissions", tags=["RBAC"])


@router.get("", response_model=list[PermissionOut])
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
    return [PermissionOut.model_validate(p) for p in permissions]
