"""SSO Settings router - HTTP handlers for SSO provider management (admin only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastcrud import PaginatedListResponse, compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser
from ...core.db.database import async_get_db
from .crud import crud_sso_settings
from .schemas import (
    SSOSettingsCreate,
    SSOSettingsRead,
    SSOSettingsReadWithSecrets,
    SSOSettingsUpdate,
)

router = APIRouter(prefix="/admin/sso-settings", tags=["admin", "sso-settings"])


@router.post("/", response_model=SSOSettingsRead, status_code=201)
async def create_sso_settings(
    request: Request,
    settings: SSOSettingsCreate,
    current_superuser: Annotated[dict, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Create new SSO provider settings (superuser only)."""
    from .service import sso_settings_service

    existing = await crud_sso_settings.exists(db=db, provider=settings.provider)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSO settings for provider {settings.provider} already exist",
        )

    return await sso_settings_service.create_settings(db, settings, user_id=current_superuser["id"])


@router.get("/", response_model=PaginatedListResponse[SSOSettingsRead], dependencies=[Depends(get_current_superuser)])
async def list_sso_settings(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = 1,
    items_per_page: int = 10,
) -> dict:
    """List all SSO provider settings (superuser only)."""
    settings_data = await crud_sso_settings.get_multi(
        db=db,
        offset=compute_offset(page, items_per_page),
        limit=items_per_page,
    )

    response = paginated_response(crud_data=settings_data, page=page, items_per_page=items_per_page)
    return response


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
) -> dict:
    """Update SSO settings for a specific provider (superuser only)."""
    from .service import sso_settings_service

    existing = await crud_sso_settings.exists(db=db, provider=provider)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO settings for provider {provider} not found",
        )

    return await sso_settings_service.update_settings(db, provider, values, user_id=current_superuser["id"])


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
