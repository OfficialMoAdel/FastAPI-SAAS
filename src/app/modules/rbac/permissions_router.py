"""
Permissions Router

Provides endpoints for listing all available permissions in the system.
This is separate from the roles router to match the old backend's API structure.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.rbac.router import get_role_service
from src.app.modules.rbac.schemas import PermissionList
from src.app.modules.rbac.service import RoleService

router = APIRouter(prefix="/permissions", tags=["rbac"])


@router.get("", response_model=PermissionList, dependencies=[Depends(requires_permission(Permission.ROLES_READ))])
async def list_permissions(
    service: Annotated[RoleService, Depends(get_role_service)],
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
):
    """List all available permissions in the system (paginated).

    Returns all permissions that can be assigned to roles.
    This is useful for building role management UIs where admins
    can select which permissions to grant to a role.

    **Requires:** Authentication (any authenticated user can list permissions)
    """
    permissions = await service.get_all_permissions()
    all_permissions = list(permissions)
    start = pagination.offset
    page_items = all_permissions[start : start + pagination.limit]
    return build_paginated_response(
        items=page_items, total=len(all_permissions), page=pagination.page, per_page=pagination.per_page
    )
