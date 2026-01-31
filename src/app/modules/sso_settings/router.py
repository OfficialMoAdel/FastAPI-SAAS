"""SSO Settings router - HTTP handlers for SSO provider management (admin only)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser
from ...core.db.database import async_get_db
from .crud import crud_sso_settings
from .schemas import (
    SSOSettingsCreate,
    SSOSettingsList,
    SSOSettingsRead,
    SSOSettingsReadWithSecrets,
    SSOSettingsUpdate,
)

router = APIRouter(prefix="/ssos", tags=["ssos"])


@router.post("/", response_model=SSOSettingsRead, status_code=201)
async def create_sso_settings(
    request: Request,
    settings: SSOSettingsCreate,
    current_superuser: Annotated[dict, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> SSOSettingsRead:
    """Create new SSO provider settings (superuser only)."""
    from .service import sso_settings_service

    existing = await crud_sso_settings.exists(db=db, provider=settings.provider)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSO settings for provider {settings.provider} already exist",
        )

    result = await sso_settings_service.create_settings(db, settings, user_id=current_superuser["id"])
    return SSOSettingsRead.model_validate(result)


@router.get("/", response_model=SSOSettingsList, dependencies=[Depends(get_current_superuser)])
async def list_sso_settings(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """List all SSO provider settings (superuser only)."""
    offset = (page - 1) * per_page
    settings_data = await crud_sso_settings.get_multi(
        db=db,
        offset=offset,
        limit=per_page,
        schema_to_select=SSOSettingsRead,
    )

    data_raw = settings_data.get("data", [])
    data: list[dict[str, Any]] = data_raw if isinstance(data_raw, list) else []
    total_count = settings_data.get("total_count")
    total: int = total_count if isinstance(total_count, int) else len(data)
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/available-providers", response_model=list[str])
async def list_available_providers() -> list[str]:
    """Get list of available SSO providers.

    Returns a static list of SSO providers that can be configured.
    This endpoint is accessible to superusers for setting up new SSO providers.
    """
    return [
        "google",
        "github",
        "apple",
        "microsoft",
        "facebook",
        "gitlab",
        "bitbucket",
        "slack",
    ]


@router.get("/{provider}", response_model=SSOSettingsReadWithSecrets, dependencies=[Depends(get_current_superuser)])
async def get_sso_settings(
    request: Request,
    provider: str,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get SSO settings for a specific provider (superuser only)."""
    from .service import sso_settings_service

    settings = await sso_settings_service.get_decrypted_settings(db, provider)
    if not settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )
    return settings


@router.patch("/{provider}", response_model=SSOSettingsRead, dependencies=[Depends(get_current_superuser)])
async def update_sso_settings(
    request: Request,
    provider: str,
    values: SSOSettingsUpdate,
    current_superuser: Annotated[dict, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> SSOSettingsRead:
    """Update SSO settings for a specific provider (superuser only)."""
    from .service import sso_settings_service

    existing = await crud_sso_settings.exists(db=db, provider=provider)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )

    result = await sso_settings_service.update_settings(db, provider, values, user_id=current_superuser["id"])
    return SSOSettingsRead.model_validate(result)


@router.delete("/{provider}", status_code=200, dependencies=[Depends(get_current_superuser)])
async def delete_sso_settings(
    request: Request,
    provider: str,
    current_superuser: Annotated[dict, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    """Delete SSO settings for a specific provider (superuser only)."""
    from .service import sso_settings_service

    existing = await crud_sso_settings.exists(db=db, provider=provider)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )

    await sso_settings_service.delete_settings(db, provider, user_id=current_superuser["id"])
    return {"message": f"SSO settings for provider {provider} deleted successfully"}
