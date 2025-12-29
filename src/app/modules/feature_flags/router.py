"""Feature Flags API Router."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.feature_flags.schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from src.app.modules.feature_flags.utils import (
    bulk_update_feature_flags,
    get_feature_flags,
    is_feature_enabled,
)
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

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


@router.patch("/{flag_name}", response_model=FeatureFlagCheckResponse)
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

    NOTE: This endpoint should be protected with admin-only permissions
    in production. Feature flags are typically managed by admins.
    """
    # TODO: Add RBAC check for admin permission
    # from src.app.modules.rbac.dependencies import require_permission
    # require_permission("feature_flags.manage")

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


@router.delete("/{flag_name}", status_code=204)
async def delete_feature_flag(
    flag_name: str,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Remove a feature flag from the tenant configuration.

    NOTE: This endpoint should be protected with admin-only permissions.
    """
    # TODO: Add RBAC check for admin permission

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


@router.put("/bulk", response_model=FeatureFlagsResponse)
async def bulk_update_flags(
    request: FeatureFlagBulkUpdateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagsResponse:
    """
    Bulk update feature flags for the current tenant.
    """
    # TODO: Add RBAC check for admin permission

    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    await bulk_update_feature_flags(db, tenant_id, request.feature_flags)

    # Return updated flags
    flags = await get_feature_flags(db, tenant_id)
    return FeatureFlagsResponse(flags=flags)
