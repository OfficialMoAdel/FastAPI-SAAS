"""Feature Flags API Router."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules._base import ModelNotFoundError
from src.app.modules.feature_flags.schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from src.app.modules.feature_flags.service import FeatureFlagService, get_feature_flag_service
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

router = APIRouter(prefix="/features", tags=["features"])


from uuid import UUID


def _get_tenant_id(current_tenant: Tenant) -> UUID:
    """Extract tenant ID from tenant object."""
    return current_tenant.id


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
        enabled=bool(value),
        value=value,
    )


from fastapi import Response


@router.delete(
    "/{flag_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def delete_feature_flag(
    flag_name: str,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Remove a feature flag from the tenant configuration.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    try:
        await service.delete_tenant_flag(tenant_id, flag_name)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
