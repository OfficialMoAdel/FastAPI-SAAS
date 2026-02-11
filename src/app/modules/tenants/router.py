"""Tenant API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import ForbiddenException
from .schemas import TenantCreate, TenantList, TenantResponse, TenantUpdate
from .service import TenantService, get_tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("", response_model=TenantList)
async def list_tenants(
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=100),
    is_active: bool | None = Query(default=None),
) -> dict[str, Any]:
    """List all tenants with pagination.

    Requires authentication. Returns paginated list of tenants.
    """
    tenants_data = await service.list_tenants(
        page=page,
        items_per_page=per_page,
        is_active=is_active,
    )
    data = tenants_data.get("data", [])
    total = tenants_data.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    tenant: TenantCreate,
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Create a new tenant.

    Requires authentication. Slug must be unique.
    Returns 409 if slug already exists.
    """
    return await service.create_tenant(tenant)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: int,
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get a tenant by ID.

    Requires authentication. Returns 404 if not found.
    """
    if not _current_user.get("is_superuser") and _current_user.get("tenant_id") != tenant_id:
        raise ForbiddenException(detail="You do not have permission to access this tenant.")

    return await service.get_tenant(tenant_id)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: int,
    values: TenantUpdate,
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a tenant.

    Requires authentication. Returns 404 if not found.
    Can update name and is_active status.
    """
    if not _current_user.get("is_superuser") and _current_user.get("tenant_id") != tenant_id:
        raise ForbiddenException(detail="You do not have permission to update this tenant.")

    return await service.update_tenant(tenant_id, values)


@router.post("/{tenant_id}/deactivate", response_model=TenantResponse)
async def deactivate_tenant(
    tenant_id: int,
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Deactivate a tenant.

    Requires authentication. Returns 404 if not found.
    Deactivated tenants will be rejected by the middleware.
    """
    return await service.deactivate_tenant(tenant_id)


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: int,
    service: Annotated[TenantService, Depends(lambda db=Depends(async_get_db): get_tenant_service(db))],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Reactivate a deactivated tenant.

    Requires authentication. Returns 404 if not found.
    """
    return await service.reactivate_tenant(tenant_id)
