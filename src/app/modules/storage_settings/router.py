"""Storage Settings API router.

This module defines the API endpoints for managing tenant storage settings.
All endpoints require MANAGE_STORAGE_SETTINGS permission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
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
)
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

router = APIRouter(prefix="/storage-settings", tags=["Storage Settings"])


def require_storage_permission(user: User = Depends(get_current_user)) -> User:
    """Dependency to check MANAGE_STORAGE_SETTINGS permission.

    Args:
        user: Current authenticated user.

    Returns:
        User if authorized.

    Raises:
        HTTPException: If user lacks permission.
    """
    # Check if user has MANAGE_STORAGE_SETTINGS permission
    if not hasattr(user, "has_permission"):
        # Fallback: check roles directly
        has_perm = any(
            Permission.MANAGE_STORAGE_SETTINGS.value in [p.value for p in role.permissions]
            for role in (user.roles or [])
        )
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MANAGE_STORAGE_SETTINGS permission required",
            )
    elif not user.has_permission(Permission.MANAGE_STORAGE_SETTINGS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MANAGE_STORAGE_SETTINGS permission required",
        )
    return user


@router.get("", response_model=StorageSettingsRead)
async def get_storage_settings(
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_storage_permission),
) -> StorageSettingsRead:
    """Get storage settings for the current tenant.

    Returns the current storage configuration with masked credentials.

    Returns:
        StorageSettingsRead: Current storage settings.

    Raises:
        404: If storage settings not configured.
        403: If user lacks MANAGE_STORAGE_SETTINGS permission.
    """
    service = StorageSettingsService(db)

    settings = await service.get_settings(tenant.id)
    if not settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage settings not configured for this tenant",
        )

    return service.to_read_schema(settings)


@router.post("", response_model=StorageSettingsRead, status_code=status.HTTP_201_CREATED)
async def create_storage_settings(
    data: StorageSettingsCreate,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_storage_permission),
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
    service = StorageSettingsService(db)

    # Check if settings already exist
    existing = await service.get_settings(tenant.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Storage settings already exist for this tenant. Use PATCH to update.",
        )

    try:
        settings = await service.create_settings(tenant.id, data)
        return service.to_read_schema(settings)

    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch("", response_model=StorageSettingsRead)
async def update_storage_settings(
    data: StorageSettingsUpdate,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_storage_permission),
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
    service = StorageSettingsService(db)

    try:
        settings = await service.update_settings(tenant.id, data)
        return service.to_read_schema(settings)

    except StorageNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage settings not configured for this tenant",
        )

    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


__all__ = ["router"]
