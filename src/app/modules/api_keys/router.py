"""API Keys router - HTTP endpoints for API key management."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.api_keys.schemas import (
    APIKeyCreate,
    APIKeyList,
    APIKeyResponse,
    APIKeyRevokeResponse,
    APIKeyRotateResponse,
)
from src.app.modules.api_keys.service import APIKeyService, get_api_key_service

router = APIRouter(
    prefix="/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(requires_permission(Permission.USERS_MANAGE))],
)


async def _get_api_key_service(db: Annotated[AsyncSession, Depends(async_get_db)]) -> APIKeyService:
    """Dependency for APIKeyService injection."""
    return get_api_key_service(db)


# Type alias for dependency injection
APIKeyServiceDep = Annotated[APIKeyService, Depends(_get_api_key_service)]


@router.post("", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    ctx: RequestContextDep,
    body: APIKeyCreate,
    service: APIKeyServiceDep,
) -> APIKeyResponse:
    """Create a new API key.

    The full API key is only returned once at creation time.
    Store it securely as it cannot be retrieved again.
    """
    return await service.create_api_key(
        api_key_data=body,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
    )


@router.get("", response_model=APIKeyList)
async def list_api_keys(
    ctx: RequestContextDep,
    service: APIKeyServiceDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
) -> Any:
    """List API keys for the current user.

    Returns key metadata only - the actual keys are never returned after creation.
    """
    result = await service.list_api_keys(
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        page=page,
        items_per_page=per_page,
    )
    data = result.get("data", [])
    total = result.get("total_count", 0)
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.delete("/{key_id}", response_model=APIKeyRevokeResponse)
async def revoke_api_key(
    key_id: int,
    ctx: RequestContextDep,
    service: APIKeyServiceDep,
) -> APIKeyRevokeResponse:
    """Revoke an API key.

    This immediately invalidates the key. This action cannot be undone.
    """
    result = await service.revoke_api_key(
        key_id=key_id,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
    )
    return APIKeyRevokeResponse(message=result["message"])


@router.post("/{key_id}/rotate", response_model=APIKeyRotateResponse)
async def rotate_api_key(
    key_id: int,
    ctx: RequestContextDep,
    service: APIKeyServiceDep,
    grace_period_days: int = 7,
) -> APIKeyRotateResponse:
    """Rotate an API key with grace period for zero-downtime rotation.

    Creates a new key that becomes valid after the grace period.
    The old key remains valid during the grace period, allowing
    seamless transition without service interruption.

    After the grace period expires, only the new key will work.
    Both keys can be used during the grace period.

    Parameters
    ----------
    key_id : int
        ID of the API key to rotate
    grace_period_days : int
        Days before new key becomes valid (default: 7)

    Returns
    -------
    APIKeyRotateResponse
        New API key with grace period information
    """
    result = await service.rotate_api_key(
        key_id=key_id,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        grace_period_days=grace_period_days,
    )
    return APIKeyRotateResponse(
        id=result.id,
        name=result.name,
        key=result.key,
        key_prefix=result.key_prefix,
        expires_at=result.expires_at,
        created_at=result.created_at,
        message=result.message,
    )
