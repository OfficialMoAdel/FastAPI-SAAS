"""Feature Flags API Router."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules._base import ModelNotFoundError
from src.app.modules.feature_flags.schemas import (
    FeatureFlagBulkUpdateRequest,
    FeatureFlagCheckResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdateRequest,
)
from src.app.modules.feature_flags.service import FeatureFlagService

router = APIRouter(prefix="/features", tags=["features"])


def get_feature_flag_service(
    db: AsyncSession = Depends(async_get_db),
) -> FeatureFlagService:
    """Dependency injection factory for FeatureFlagService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    return FeatureFlagService(db=db)


@router.get(
    "",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
async def list_feature_flags(
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> FeatureFlagsResponse:
    """
    Get all feature flags for the current tenant.

    Returns the complete feature_flags JSONB object from tenant record.
    """
    flags = await service.get_tenant_flags(ctx.tenant_id)

    return FeatureFlagsResponse(feature_flags=flags)


@router.get(
    "/{flag_name}",
    response_model=FeatureFlagCheckResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
async def check_feature_flag(
    flag_name: str,
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> FeatureFlagCheckResponse:
    """
    Check if a specific feature flag is enabled.

    Returns the value of the flag (can be boolean, string, number, etc.).
    Returns null if flag doesn't exist.
    """
    enabled = await service.is_enabled(ctx.tenant_id, flag_name)

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
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> FeatureFlagCheckResponse:
    """
    Update a specific feature flag for the current tenant.

    Value can be boolean, string, number, or any JSON-serializable type.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = ctx.require_tenant().tenant_id

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


@router.delete(
    "/{flag_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_MANAGE))],
)
async def delete_feature_flag(
    flag_name: str,
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> Response:
    """
    Remove a feature flag from the tenant configuration.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = ctx.require_tenant().tenant_id

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
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> FeatureFlagsResponse:
    """
    Bulk update feature flags for the current tenant.

    Requires FEATURE_FLAGS_MANAGE permission.
    """
    tenant_id = ctx.require_tenant().tenant_id

    try:
        await service.bulk_update_tenant_flags(tenant_id, request.feature_flags)
    except ModelNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found.")

    # Return updated flags
    flags = await service.get_tenant_flags(tenant_id)
    return FeatureFlagsResponse(feature_flags=flags)
