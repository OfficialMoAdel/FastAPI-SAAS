"""API Keys router - HTTP endpoints for API key management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.tenancy.context import get_tenant_context
from .schemas import (
    APIKeyCreate,
    APIKeyList,
    APIKeyResponse,
    APIKeyRevokeResponse,
    APIKeyRotateResponse,
)
from .service import APIKeyService, get_api_key_service

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


async def _get_api_key_service(db: Annotated[AsyncSession, Depends(async_get_db)]) -> APIKeyService:
    """Dependency for APIKeyService injection."""
    return get_api_key_service(db)


# Type alias for dependency injection
APIKeyServiceDep = Annotated[APIKeyService, Depends(_get_api_key_service)]


def _get_tenant_id() -> UUID:
    """Get current tenant ID from context."""
    context = get_tenant_context()
    if context is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Tenant context not set")
    return context.id


@router.post("", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: APIKeyCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[UUID, Depends(_get_tenant_id)],
    service: APIKeyServiceDep,
) -> APIKeyResponse:
    """Create a new API key.

    The full API key is only returned once at creation time.
    Store it securely as it cannot be retrieved again.
    """
    return await service.create_api_key(
        api_key_data=body,
        user_id=current_user["id"],
        tenant_id=tenant_id,
    )


@router.get("", response_model=APIKeyList)
async def list_api_keys(
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[UUID, Depends(_get_tenant_id)],
    service: APIKeyServiceDep,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """List API keys for the current user.

    Returns key metadata only - the actual keys are never returned after creation.
    """
    result = await service.list_api_keys(
        user_id=current_user["id"],
        tenant_id=tenant_id,
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
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[UUID, Depends(_get_tenant_id)],
    service: APIKeyServiceDep,
) -> APIKeyRevokeResponse:
    """Revoke an API key.

    This immediately invalidates the key. This action cannot be undone.
    """
    result = await service.revoke_api_key(
        key_id=key_id,
        user_id=current_user["id"],
        tenant_id=tenant_id,
    )
    return APIKeyRevokeResponse(message=result["message"])


@router.post("/{key_id}/rotate", response_model=APIKeyRotateResponse)
async def rotate_api_key(
    key_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[UUID, Depends(_get_tenant_id)],
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
        user_id=current_user["id"],
        tenant_id=tenant_id,
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
