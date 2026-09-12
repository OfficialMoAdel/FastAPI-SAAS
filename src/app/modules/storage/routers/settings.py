"""Storage Settings API router.

This module defines the API endpoints for managing tenant storage settings.
All endpoints require MANAGE_STORAGE_SETTINGS permission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.storage.errors import (
    InvalidCredentialsError,
    StorageNotConfiguredError,
)
from src.app.modules.storage.schemas import (
    StorageSettingsCreate,
    StorageSettingsRead,
    StorageSettingsUpdate,
)
from src.app.modules.storage.service import StorageAdminService
from src.app.shared.exceptions import DuplicateError, ValidationError

router = APIRouter(prefix="/storage", tags=["storage"])


def get_storage_admin(db: AsyncSession = Depends(async_get_db)) -> StorageAdminService:
    """Dependency injection factory for StorageAdminService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    return StorageAdminService(db)


def get_storage_settings_service(db: AsyncSession = Depends(async_get_db)) -> StorageAdminService:
    """Backward-compatible dependency factory for StorageSettingsService."""
    return StorageAdminService(db)


@router.get(
    "",
    response_model=StorageSettingsRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_STORAGE_SETTINGS))],
)
async def get_storage_settings(
    ctx: RequestContextDep,
    service: StorageAdminService = Depends(get_storage_admin),
) -> StorageSettingsRead:
    """Get storage settings for the current tenant."""
    tenant = ctx.require_tenant()
    storage_settings = await service.get_settings(tenant.tenant_id)
    if not storage_settings:
        raise StorageNotConfiguredError("Storage settings not configured for this tenant")

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
    service: StorageAdminService = Depends(get_storage_admin),
) -> StorageSettingsRead:
    """Create storage settings for the current tenant."""
    tenant = ctx.require_tenant()
    existing = await service.get_settings(tenant.tenant_id)
    if existing:
        raise DuplicateError(
            "storage_settings",
            str(tenant.tenant_id),
            message="Storage settings already exist for this tenant. Use PATCH to update.",
        )

    try:
        storage_settings = await service.create_settings(tenant.tenant_id, data)
        return service.to_read_schema(storage_settings)

    except InvalidCredentialsError:
        # Straight-through: the central table maps it to 400.
        raise

    except IntegrityError as e:
        # Create race: a concurrent request inserted first.
        raise DuplicateError(
            "storage_settings",
            str(tenant.tenant_id),
            message="Storage settings already exist for this tenant. Use PATCH to update.",
        ) from e

    except ValueError as e:
        raise ValidationError("Invalid storage settings payload.") from e


@router.patch(
    "",
    response_model=StorageSettingsRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requires_permission(Permission.MANAGE_STORAGE_SETTINGS))],
)
async def update_storage_settings(
    data: StorageSettingsUpdate,
    ctx: RequestContextDep,
    service: StorageAdminService = Depends(get_storage_admin),
) -> StorageSettingsRead:
    """Update storage settings for the current tenant."""
    tenant = ctx.require_tenant()
    try:
        storage_settings = await service.update_settings(tenant.tenant_id, data)
        return service.to_read_schema(storage_settings)

    except StorageNotConfiguredError:
        # Straight-through: the central table maps it to 400.
        raise

    except InvalidCredentialsError:
        # Straight-through: the central table maps it to 400.
        raise

    except ValueError as e:
        raise ValidationError("Invalid storage settings payload.") from e


__all__ = [
    "create_storage_settings",
    "get_storage_settings",
    "router",
    "update_storage_settings",
]
