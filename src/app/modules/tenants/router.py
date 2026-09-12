"""Tenant API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.tenancy.context import require_tenant_match
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.tenants import TenantService
from src.app.modules.tenants.schemas import TenantCreate, TenantList, TenantResponse, TenantUpdate

router = APIRouter(prefix="/tenants", tags=["tenants"])


def get_tenant_service(db: AsyncSession = Depends(async_get_db)) -> TenantService:
    """Dependency injection factory for TenantService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    (Absorbs the old `_get_tenant_service` ctx wrapper, M-4.)
    """
    return TenantService(db)


TenantServiceDep = Annotated[TenantService, Depends(get_tenant_service)]


@router.get("", response_model=TenantList, dependencies=[Depends(get_current_superuser)])
async def list_tenants(
    service: TenantServiceDep,
    ctx: RequestContextDep,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=100),
    is_active: bool | None = Query(default=None),
) -> dict[str, Any]:
    """List all tenants with pagination.

    Requires superuser. Returns paginated list of tenants.
    """
    tenants_data = await service.list_tenants(
        page=page,
        items_per_page=per_page,
        is_active=is_active,
    )
    data = tenants_data.get("data", [])
    total = tenants_data.get("total_count", 0)
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def create_tenant(
    tenant: TenantCreate,
    service: TenantServiceDep,
    ctx: RequestContextDep,
) -> dict[str, Any]:
    """Create a new tenant.

    Requires superuser. Slug must be unique.
    Returns 409 if slug already exists.
    """
    return await service.create_tenant(tenant)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    service: TenantServiceDep,
    ctx: RequestContextDep,
) -> dict[str, Any]:
    """Get a tenant by ID.

    Requires authentication. Returns 404 if not found.
    """
    user_tenant_id = get_user_attr(ctx.user, "tenant_id")
    if not ctx.is_superuser:
        # P1 FIX (code-review): enumeration-safe soft-404 via the canonical
        # seam instead of ad-hoc 403 (403 vs 404 leaked tenant existence).
        require_tenant_match(tenant_id, resource="Tenant", key=tenant_id)
        if str(user_tenant_id) != str(tenant_id):
            raise ForbiddenException(detail="You do not have permission to access this tenant.")

    return await service.get_tenant(tenant_id)


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    dependencies=[Depends(requires_permission(Permission.TENANT_UPDATE))],
)
async def update_tenant(
    tenant_id: UUID,
    values: TenantUpdate,
    service: TenantServiceDep,
    ctx: RequestContextDep,
) -> dict[str, Any]:
    """Update a tenant.

    Requires TENANT_UPDATE permission. Returns 404 if not found.
    Can update name and is_active status.
    """
    user_tenant_id = get_user_attr(ctx.user, "tenant_id")
    if not ctx.is_superuser:
        # P1 FIX (code-review): same soft-404-first ordering as get_tenant.
        require_tenant_match(tenant_id, resource="Tenant", key=tenant_id)
        if str(user_tenant_id) != str(tenant_id):
            raise ForbiddenException(detail="You do not have permission to update this tenant.")

    return await service.update_tenant(tenant_id, values)


@router.post(
    "/{tenant_id}/deactivate",
    response_model=TenantResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def deactivate_tenant(
    tenant_id: UUID,
    service: TenantServiceDep,
    ctx: RequestContextDep,
) -> dict[str, Any]:
    """Deactivate a tenant.

    Requires superuser. Returns 404 if not found.
    Deactivated tenants will be rejected by the middleware.
    """
    return await service.deactivate_tenant(tenant_id)


@router.post(
    "/{tenant_id}/reactivate",
    response_model=TenantResponse,
    dependencies=[Depends(get_current_superuser)],
)
async def reactivate_tenant(
    tenant_id: UUID,
    service: TenantServiceDep,
    ctx: RequestContextDep,
) -> dict[str, Any]:
    """Reactivate a deactivated tenant.

    Requires superuser. Returns 404 if not found.
    """
    return await service.reactivate_tenant(tenant_id)
