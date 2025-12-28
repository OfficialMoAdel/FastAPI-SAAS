"""Tenant API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastcrud import paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from .schemas import TenantCreate, TenantListResponse, TenantResponse, TenantUpdate
from .service import tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    items_per_page: int = Query(default=10, ge=1, le=100),
    is_active: bool | None = Query(default=None),
) -> dict[str, Any]:
    """List all tenants with pagination.

    Requires authentication. Returns paginated list of tenants.
    """
    tenants_data = await tenant_service.list_tenants(
        db=db,
        page=page,
        items_per_page=items_per_page,
        is_active=is_active,
    )

    response: dict[str, Any] = paginated_response(
        crud_data=tenants_data,
        page=page,
        items_per_page=items_per_page,
    )
    return response


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    tenant: TenantCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a new tenant.

    Requires authentication. Slug must be unique.
    Returns 409 if slug already exists.
    """
    return await tenant_service.create_tenant(tenant, db)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get a tenant by ID.

    Requires authentication. Returns 404 if not found.
    """
    return await tenant_service.get_tenant(tenant_id, db)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: int,
    values: TenantUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a tenant.

    Requires authentication. Returns 404 if not found.
    Can update name and is_active status.
    """
    return await tenant_service.update_tenant(tenant_id, values, db)


@router.post("/{tenant_id}/deactivate", response_model=TenantResponse)
async def deactivate_tenant(
    tenant_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Deactivate a tenant.

    Requires authentication. Returns 404 if not found.
    Deactivated tenants will be rejected by the middleware.
    """
    return await tenant_service.deactivate_tenant(tenant_id, db)


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Reactivate a deactivated tenant.

    Requires authentication. Returns 404 if not found.
    """
    return await tenant_service.reactivate_tenant(tenant_id, db)
