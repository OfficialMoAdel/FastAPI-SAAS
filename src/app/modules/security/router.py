"""
Security Router

Provides convenience endpoints for security-related operations.
This router acts as a thin wrapper around the passkey module to maintain
backward compatibility with the old backend's /api/v1/security/* routes.

Endpoints:
- GET /security/passkeys - List user's passkeys
- DELETE /security/passkeys/{passkey_id} - Delete a passkey
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.passkey.schemas import (
    PasskeyCredentialResponse,
    PasskeyDeleteResponse,
)
from src.app.modules.passkey.service import passkey_service

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/passkeys", response_model=list[PasskeyCredentialResponse])
async def list_passkeys(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """List user's passkey credentials.

    This is a convenience endpoint that wraps /auth/passkey/credentials
    for backward compatibility with the old backend.
    """
    return await passkey_service.list_credentials(db=db, user_id=current_user["id"])


@router.delete("/passkeys/{passkey_id}", response_model=PasskeyDeleteResponse)
async def delete_passkey(
    passkey_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """Delete a passkey credential.

    This is a convenience endpoint that wraps /auth/passkey/credentials/{credential_id}
    for backward compatibility with the old backend.
    """
    await passkey_service.delete_credential(db=db, user_id=current_user["id"], credential_id=passkey_id)
    return PasskeyDeleteResponse(message="Passkey deleted successfully")
