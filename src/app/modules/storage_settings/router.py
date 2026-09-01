"""Storage Settings API router.

This module defines the API endpoints for managing tenant storage settings.
All endpoints require MANAGE_STORAGE_SETTINGS permission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.app.api.deps import RequestContextDep
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.storage_settings.schemas import (
    StorageSettingsCreate,
    StorageSettingsRead,
    StorageSettingsUpdate,
)
from src.app.modules.storage_settings.service import (
    InvalidCredentialsError,
    StorageNotConfiguredError,
    StorageSettingsService,
    get_storage_settings_service,
)

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get(
    "",
    response_model=StorageSettingsRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_STORAGE_SETTINGS))],
)
async def get_storage_settings(
    ctx: RequestContextDep,
    service: StorageSettingsService = Depends(get_storage_settings_service),
) -> StorageSettingsRead:
    """Get storage settings for the current tenant.

    Returns the current storage configuration with masked credentials.

    Returns:
        StorageSettingsRead: Current storage settings.

    Raises:
        404: If storage settings not configured.
        403: If user lacks MANAGE_STORAGE_SETTINGS permission.
    """
    tenant = ctx.require_tenant()
    storage_settings = await service.get_settings(tenant.tenant_id)
    if not storage_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage settings not configured for this tenant",
        )

    return service.to_read_schema(storage_settings)


@router.post(
    "",
    response_model=StorageSettingsRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_STORAGE_SETTINGS))],
)
async def create_storage_settings(
    data: StorageSettingsCreate,
    ctx: RequestContextDep,
    service: StorageSettingsService = Depends(get_storage_settings_service),
) -> StorageSettingsRead:
    """Create storage settings for the current tenant.

    Validates credentials before saving (tests connection to provider).

    Args:
        data: Storage settings configuration.

    Returns:
        StorageSettingsRead: Created storage settings.

    Raises:
        400: If credentials are invalid or validation fails.
        403: If user lacks MANAGE_STORAGE_SETTINGS permission.
        409: If settings already exist for this tenant.
    """
    tenant = ctx.require_tenant()
    # Check if settings already exist
    existing = await service.get_settings(tenant.tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Storage settings already exist for this tenant. Use PATCH to update.",
        )

    try:
        storage_settings = await service.create_settings(tenant.tenant_id, data)
        return service.to_read_schema(storage_settings)

    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage provider credentials are invalid or could not be validated.",
        )

    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage settings payload.",
        )


@router.patch(
    "",
    response_model=StorageSettingsRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requires_permission(Permission.MANAGE_STORAGE_SETTINGS))],
)
async def update_storage_settings(
    data: StorageSettingsUpdate,
    ctx: RequestContextDep,
    service: StorageSettingsService = Depends(get_storage_settings_service),
) -> StorageSettingsRead:
    """Update storage settings for the current tenant.

    Only provided fields are updated. Re-validates credentials if changed.

    Args:
        data: Fields to update.

    Returns:
        StorageSettingsRead: Updated storage settings.

    Raises:
        400: If credentials are invalid or validation fails.
        403: If user lacks MANAGE_STORAGE_SETTINGS permission.
        404: If storage settings not configured.
    """
    tenant = ctx.require_tenant()
    try:
        storage_settings = await service.update_settings(tenant.tenant_id, data)
        return service.to_read_schema(storage_settings)

    except StorageNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage settings not configured for this tenant",
        )

    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage provider credentials are invalid or could not be validated.",
        )

    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage settings payload.",
        )


__all__ = ["router"]
