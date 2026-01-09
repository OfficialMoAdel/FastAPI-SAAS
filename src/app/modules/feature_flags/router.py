"""Feature Flags API Router."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ...api.dependencies import get_current_tenant, get_current_user
from ...core.rbac.dependencies import requires_permission
from ...core.rbac.permissions import Permission
from ...modules.tenants.models import Tenant
from ...modules.users.models import User
from .._base import ModelNotFoundError
from .schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from .service import FeatureFlagService, get_feature_flag_service

router = APIRouter(prefix="/feature-flags", tags=["Feature Flags"])


def _get_tenant_id(current_tenant: Tenant | int) -> Any:
    """Extract tenant ID from tenant object or int."""
    return current_tenant.id if hasattr(current_tenant, "id") else current_tenant


@router.get("", response_model=FeatureFlagsResponse)
async def list_feature_flags(
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagsResponse:
    """
    Get all feature flags for the current tenant.

    Returns the complete feature_flags JSONB object from tenant record.
    """
    tenant_id = _get_tenant_id(current_tenant)
    flags = await service.get_tenant_flags(tenant_id)

    return FeatureFlagsResponse(feature_flags=flags)


@router.get("/{flag_name}", response_model=FeatureFlagCheckResponse)
async def check_feature_flag(
    flag_name: str,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagCheckResponse:
    """
    Check if a specific feature flag is enabled.

    Returns the value of the flag (can be boolean, string, number, etc.).
    Returns null if flag doesn't exist.
    """
    tenant_id = _get_tenant_id(current_tenant)
    enabled = await service.is_enabled(tenant_id, flag_name)

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
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagCheckResponse:
    """
    Update a specific feature flag for the current tenant.

    Value can be boolean, string, number, or any JSON-serializable type.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    # Determine value to set
    value = request.value if request.value is not None else request.enabled

    try:
        await service.update_tenant_flag(tenant_id, flag_name, value)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return FeatureFlagCheckResponse(
        flag_name=flag_name,
        enabled=value if isinstance(value, bool) else True,
        value=value,
    )


@router.delete(
    "/{flag_name}",
    status_code=204,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def delete_feature_flag(
    flag_name: str,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Remove a feature flag from the tenant configuration.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    try:
        await service.delete_tenant_flag(tenant_id, flag_name)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put(
    "/bulk",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def bulk_update_flags(
    request: FeatureFlagBulkUpdateRequest,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> FeatureFlagsResponse:
    """
    Bulk update feature flags for the current tenant.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    try:
        await service.bulk_update_tenant_flags(tenant_id, request.feature_flags)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Return updated flags
    flags = await service.get_tenant_flags(tenant_id)
    return FeatureFlagsResponse(feature_flags=flags)
