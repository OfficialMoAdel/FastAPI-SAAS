"""API Keys router - HTTP endpoints for API key management."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.tenancy.context import get_tenant_context
from .schemas import APIKeyCreate, APIKeyListResponse, APIKeyResponse, APIKeyRevokeResponse
from .service import api_key_service

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _get_tenant_id() -> int:
    """Get current tenant ID from context."""
    context = get_tenant_context()
    if context is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Tenant context not set")
    return context.id


@router.post("", response_model=APIKeyResponse)
async def create_api_key(
    body: APIKeyCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
) -> APIKeyResponse:
    """Create a new API key.

    The full API key is only returned once at creation time.
    Store it securely as it cannot be retrieved again.
    """
    return await api_key_service.create_api_key(
        api_key_data=body,
        user_id=current_user["id"],
        tenant_id=tenant_id,
        db=db,
    )


@router.get("", response_model=APIKeyListResponse)
async def list_api_keys(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
    page: int = 1,
    items_per_page: int = 10,
) -> APIKeyListResponse:
    """List API keys for the current user.

    Returns key metadata only - the actual keys are never returned after creation.
    """
    result = await api_key_service.list_api_keys(
        user_id=current_user["id"],
        tenant_id=tenant_id,
        db=db,
        page=page,
        items_per_page=items_per_page,
    )
    return APIKeyListResponse(
        data=result.get("data", []),
        total_count=result.get("total_count", 0),
    )


@router.delete("/{key_id}", response_model=APIKeyRevokeResponse)
async def revoke_api_key(
    key_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
) -> APIKeyRevokeResponse:
    """Revoke an API key.

    This immediately invalidates the key. This action cannot be undone.
    """
    result = await api_key_service.revoke_api_key(
        key_id=key_id,
        user_id=current_user["id"],
        tenant_id=tenant_id,
        db=db,
    )
    return APIKeyRevokeResponse(message=result["message"])
