"""SSO Settings router - HTTP handlers for SSO provider management (admin only)."""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.modules.auth.crud import crud_sso_settings
from src.app.modules.auth.schemas import (
    SSOSettingsCreate,
    SSOSettingsList,
    SSOSettingsRead,
    SSOSettingsUpdate,
)

if TYPE_CHECKING:
    from src.app.modules.auth.services.sso_settings import SSOSettingsService


router = APIRouter(prefix="/ssos", tags=["ssos"])


def get_sso_settings_service(db: AsyncSession = Depends(async_get_db)) -> "SSOSettingsService":
    """Dependency injection factory for SSOSettingsService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    from src.app.modules.auth.services.sso_settings import SSOSettingsService

    return SSOSettingsService()


@router.post(
    "/",
    response_model=SSOSettingsRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_superuser)],
)
async def create_sso_settings(
    request: Request,
    settings: SSOSettingsCreate,
    ctx: RequestContextDep,
) -> Any:
    """Create new SSO provider settings (superuser only)."""
    from src.app.modules.auth.services.sso_settings import sso_settings_service

    existing = await crud_sso_settings.exists(db=ctx.db, provider=settings.provider)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSO settings for provider {settings.provider} already exist",
        )

    result = await sso_settings_service.create_settings(ctx.db, settings, user_id=ctx.user_id)
    return result


@router.get(
    "/",
    response_model=SSOSettingsList,
    dependencies=[Depends(get_current_superuser)],
)
async def list_sso_settings(
    request: Request,
    ctx: RequestContextDep,
    page: int = 1,
    per_page: int = 10,
) -> Any:
    """List all SSO provider settings (superuser only)."""
    offset = (page - 1) * per_page
    settings_data = await crud_sso_settings.get_multi(
        db=ctx.db,
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
async def list_available_providers() -> Any:
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


@router.get(
    "/{provider}",
    response_model=SSOSettingsRead,
    dependencies=[Depends(get_current_superuser)],
)
async def get_sso_settings(
    request: Request,
    provider: str,
    ctx: RequestContextDep,
) -> Any:
    """Get SSO settings for a specific provider (superuser only)."""
    from src.app.modules.auth.services.sso_settings import sso_settings_service

    settings = await sso_settings_service.get_decrypted_settings(ctx.db, provider)
    if not settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )
    return settings


@router.patch(
    "/{provider}",
    response_model=SSOSettingsRead,
    dependencies=[Depends(get_current_superuser)],
)
async def update_sso_settings(
    request: Request,
    provider: str,
    values: SSOSettingsUpdate,
    ctx: RequestContextDep,
) -> Any:
    """Update SSO settings for a specific provider (superuser only)."""
    from src.app.modules.auth.services.sso_settings import sso_settings_service

    existing = await crud_sso_settings.exists(db=ctx.db, provider=provider)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )

    result = await sso_settings_service.update_settings(ctx.db, provider, values, user_id=ctx.user_id)
    return result


@router.delete(
    "/{provider}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_current_superuser)],
)
async def delete_sso_settings(
    request: Request,
    provider: str,
    ctx: RequestContextDep,
) -> dict[str, str]:
    """Delete SSO settings for a specific provider (superuser only)."""
    from src.app.modules.auth.services.sso_settings import sso_settings_service

    existing = await crud_sso_settings.exists(db=ctx.db, provider=provider)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )

    await sso_settings_service.delete_settings(ctx.db, provider, user_id=ctx.user_id)
    return {"message": f"SSO settings for provider {provider} deleted successfully"}
