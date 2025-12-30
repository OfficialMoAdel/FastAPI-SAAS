"""Feature Flags API Router."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant, get_current_user
from ...core.db.database import async_get_db
from ...core.rbac.dependencies import requires_permission
from ...core.rbac.permissions import Permission
from ...modules.tenants.models import Tenant
from ...modules.users.models import User
from .schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from .utils import (
    bulk_update_feature_flags,
    get_feature_flags,
    is_feature_enabled,
)

router = APIRouter(prefix="/feature-flags", tags=["Feature Flags"])


@router.get("", response_model=FeatureFlagsResponse)
async def list_feature_flags(
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagsResponse:
    """
    Get all feature flags for the current tenant.

    Returns the complete feature_flags JSONB object from tenant record.
    """
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    flags = await get_feature_flags(db, tenant_id)

    return FeatureFlagsResponse(flags=flags)


@router.get("/{flag_name}", response_model=FeatureFlagCheckResponse)
async def check_feature_flag(
    flag_name: str,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagCheckResponse:
    """
    Check if a specific feature flag is enabled.

    Returns the value of the flag (can be boolean, string, number, etc.).
    Returns null if flag doesn't exist.
    """
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    enabled = await is_feature_enabled(db, tenant_id, flag_name)

    return FeatureFlagCheckResponse(
        flag_name=flag_name,
        enabled=enabled,
    )


@router.patch(
    "/{flag_name}",
    response_model=FeatureFlagCheckResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def update_feature_flag(
    flag_name: str,
    request: FeatureFlagUpdateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagCheckResponse:
    """
    Update a specific feature flag for the current tenant.

    Value can be boolean, string, number, or any JSON-serializable type.

    Requires FEATURE_FLAGS_MANAGE permission.
    """

    # Get current tenant
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    query = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(query)
    tenant = result.scalar_one()

    # Update feature flags JSONB
    if tenant.feature_flags is None:
        tenant.feature_flags = {}

    # Create a copy to trigger SQLAlchemy change detection
    flags = dict(tenant.feature_flags)
    flags[flag_name] = request.value
    tenant.feature_flags = flags

    await db.commit()
    await db.refresh(tenant)

    return FeatureFlagCheckResponse(
        flag_name=flag_name,
        enabled=request.value,
    )


@router.delete(
    "/{flag_name}",
    status_code=204,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def delete_feature_flag(
    flag_name: str,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Remove a feature flag from the tenant configuration.

    Requires FEATURE_FLAGS_MANAGE permission.
    """

    # Get current tenant
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    query = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(query)
    tenant = result.scalar_one()

    if tenant.feature_flags is None or flag_name not in tenant.feature_flags:
        raise HTTPException(status_code=404, detail="Feature flag not found")

    # Remove flag
    flags = dict(tenant.feature_flags)
    del flags[flag_name]
    tenant.feature_flags = flags

    await db.commit()


@router.put(
    "/bulk",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def bulk_update_flags(
    request: FeatureFlagBulkUpdateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagsResponse:
    """
    Bulk update feature flags for the current tenant.

    Requires FEATURE_FLAGS_MANAGE permission.
    """

    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    await bulk_update_feature_flags(db, tenant_id, request.feature_flags)

    # Return updated flags
    flags = await get_feature_flags(db, tenant_id)
    return FeatureFlagsResponse(flags=flags)
