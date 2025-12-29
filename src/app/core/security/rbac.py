from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.modules.rbac.models import Permission, Role, role_permissions, user_roles
from src.app.modules.tenants.models import Tenant


def requires_permission(permission_slug: str):
    """
    Dependency factory to check if the current user has the required permission
    within the current tenant context.
    """

    async def _permission_checker(
        current_user: Annotated[dict, Depends(get_current_user)],
        current_tenant: Annotated[Tenant, Depends(get_current_tenant)],
        db: Annotated[AsyncSession, Depends(async_get_db)],
    ) -> bool:
        # 1. Superuser Bypass
        # Check if user is superuser. The dict returned by get_current_user should have is_superuser.
        if current_user.get("is_superuser"):
            return True

        # 2. Check Permissions
        # We need to verify if the user has a role in the current tenant
        # that includes the requested permission.

        # Query:
        # SELECT count(*)
        # FROM user_roles ur
        # JOIN roles r ON ur.role_id = r.id
        # JOIN role_permissions rp ON r.id = rp.role_id
        # JOIN permissions p ON rp.permission_id = p.id
        # WHERE ur.user_id = :user_id
        #   AND r.tenant_id = :tenant_id
        #   AND p.slug = :permission_slug

        stmt = (
            select(func.count())
            .select_from(user_roles)
            .join(Role, Role.id == user_roles.c.role_id)
            .join(role_permissions, Role.id == role_permissions.c.role_id)
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(
                user_roles.c.user_id == current_user["id"],
                Role.tenant_id == current_tenant.id,
                Permission.slug == permission_slug,
            )
        )

        result = await db.execute(stmt)
        count = result.scalar()

        if not count:
            raise ForbiddenException(detail="You do not have permission to perform this action.")

        return True

    return _permission_checker
