"""Tenant API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_superuser, get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.tenants.schemas import TenantCreate, TenantList, TenantResponse, TenantUpdate
from src.app.modules.tenants.service import TenantService, get_tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


async def _get_tenant_service(
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> TenantService:
    """Dependency factory for TenantService."""
    return get_tenant_service(db)  # type: ignore[return-value, no-any-return]


@router.get("", response_model=TenantList)
async def list_tenants(
    service: Annotated[TenantService, Depends(_get_tenant_service)],
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
    total = tenants_data.get("total_count", 0)
    return {  # type: ignore[return-value, no-any-return]
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    tenant: TenantCreate,
    service: Annotated[TenantService, Depends(_get_tenant_service)],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Create a new tenant.

    Requires authentication. Slug must be unique.
    Returns 409 if slug already exists.
    """
    return await service.create_tenant(tenant)  # type: ignore[return-value, no-any-return]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(_get_tenant_service)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get a tenant by ID.

    Requires authentication. Returns 404 if not found.
    """
    if not _current_user.get("is_superuser") and _current_user.get("tenant_id") != tenant_id:
        raise ForbiddenException(detail="You do not have permission to access this tenant.")

    return await service.get_tenant(tenant_id)  # type: ignore[return-value, no-any-return]


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    dependencies=[Depends(requires_permission(Permission.TENANT_UPDATE))],
)
async def update_tenant(
    tenant_id: UUID,
    values: TenantUpdate,
    service: Annotated[TenantService, Depends(_get_tenant_service)],
    _current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a tenant.

    Requires TENANT_UPDATE permission. Returns 404 if not found.
    Can update name and is_active status.
    """
    if not _current_user.get("is_superuser") and _current_user.get("tenant_id") != tenant_id:
        raise ForbiddenException(detail="You do not have permission to update this tenant.")

    return await service.update_tenant(tenant_id, values)  # type: ignore[return-value, no-any-return]


@router.post("/{tenant_id}/deactivate", response_model=TenantResponse)
async def deactivate_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(_get_tenant_service)],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Deactivate a tenant.

    Requires authentication. Returns 404 if not found.
    Deactivated tenants will be rejected by the middleware.
    """
    return await service.deactivate_tenant(tenant_id)  # type: ignore[return-value, no-any-return]


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(_get_tenant_service)],
    _current_user: Annotated[dict, Depends(get_current_superuser)],
) -> dict[str, Any]:
    """Reactivate a deactivated tenant.

    Requires authentication. Returns 404 if not found.
    """
    return await service.reactivate_tenant(tenant_id)  # type: ignore[return-value, no-any-return]
