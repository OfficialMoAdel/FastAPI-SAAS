"""Feature Flags API Router."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.app.api.deps import tenant_dependency
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.tenancy.context import TenantContext
from src.app.modules._base import ModelNotFoundError
from src.app.modules.feature_flags.schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from src.app.modules.feature_flags.service import FeatureFlagService, get_feature_flag_service

router = APIRouter(prefix="/features", tags=["features"])


from uuid import UUID


def _get_tenant_id(current_tenant: TenantContext) -> UUID:
    """Extract tenant ID from tenant object."""
    return current_tenant.id


@router.get(
    "",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
async def list_feature_flags(
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: TenantContext = Depends(tenant_dependency),
) -> FeatureFlagsResponse:
    """
    Get all feature flags for the current tenant.

    Returns the complete feature_flags JSONB object from tenant record.
    """
    tenant_id = _get_tenant_id(current_tenant)
    flags = await service.get_tenant_flags(tenant_id)

    return FeatureFlagsResponse(feature_flags=flags)


@router.get(
    "/{flag_name}",
    response_model=FeatureFlagCheckResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
async def check_feature_flag(
    flag_name: str,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: TenantContext = Depends(tenant_dependency),
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
    current_tenant: TenantContext = Depends(tenant_dependency),
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
    except ModelNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feature flag not found.",
        )

    return FeatureFlagCheckResponse(
        flag_name=flag_name,
        enabled=value if isinstance(value, bool) else (str(value).lower() not in ("false", "0", "no", "")),
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
    current_tenant: TenantContext = Depends(tenant_dependency),
) -> Response:
    """
    Remove a feature flag from the tenant configuration.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    try:
        await service.delete_tenant_flag(tenant_id, flag_name)
    except ModelNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feature flag not found.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/bulk",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def bulk_update_flags(
    request: FeatureFlagBulkUpdateRequest,
    service: FeatureFlagService = Depends(get_feature_flag_service),
    current_tenant: TenantContext = Depends(tenant_dependency),
) -> FeatureFlagsResponse:
    """
    Bulk update feature flags for the current tenant.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = _get_tenant_id(current_tenant)

    try:
        await service.bulk_update_tenant_flags(tenant_id, request.feature_flags)
    except ModelNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found.")

    # Return updated flags
    flags = await service.get_tenant_flags(tenant_id)
    return FeatureFlagsResponse(feature_flags=flags)
