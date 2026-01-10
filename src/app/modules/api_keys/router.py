"""API Keys router - HTTP endpoints for API key management."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.tenancy.context import get_tenant_context
from .schemas import APIKeyCreate, APIKeyList, APIKeyResponse, APIKeyRevokeResponse
from .service import APIKeyService, get_api_key_service

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

# Type alias for dependency injection
APIKeyServiceDep = Annotated[APIKeyService, Depends(lambda db=Depends(async_get_db): get_api_key_service(db))]


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
    current_user: Annotated[dict, Depends(get_current_user)],
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
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
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
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
    tenant_id: Annotated[int, Depends(_get_tenant_id)],
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
