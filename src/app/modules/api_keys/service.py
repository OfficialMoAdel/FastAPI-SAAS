"""API Key service layer - business logic for API key management."""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ForbiddenException
from .._base.exceptions import ModelNotFoundError
from .._base.service import BaseService
from .crud import crud_api_keys
from .models import APIKey
from .schemas import APIKeyCreate, APIKeyRead, APIKeyResponse


class APIKeyService(BaseService[APIKey]):
    """Service class for API key-related business logic."""

    KEY_PREFIX_LENGTH = 8
    KEY_LENGTH = 32  # 256 bits of entropy

    def __init__(self, db: AsyncSession):
        """Initialize APIKeyService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(APIKey, db)

    def _generate_key(self) -> tuple[str, str, str]:
        """Generate a new API key.

        Returns
        -------
            Tuple of (full_key, prefix, hashed_key)
        """
        # Generate a secure random key
        raw_key = secrets.token_urlsafe(self.KEY_LENGTH)
        prefix = raw_key[: self.KEY_PREFIX_LENGTH]

        # Hash the key for storage (using SHA-256)
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()

        return raw_key, prefix, hashed

    def _hash_key(self, key: str) -> str:
        """Hash an API key for comparison."""
        return hashlib.sha256(key.encode()).hexdigest()

    async def create_api_key(
        self,
        api_key_data: APIKeyCreate,
        user_id: int,
        tenant_id: int,
    ) -> APIKeyResponse:
        """Create a new API key for a user.

        Parameters
        ----------
        api_key_data : APIKeyCreate
            API key creation data
        user_id : int
            ID of the user creating the key
        tenant_id : int
            ID of the tenant

        Returns
        -------
        APIKeyResponse
            Created API key with the actual key (only shown once)
        """
        # Generate the key
        full_key, prefix, hashed_key = self._generate_key()

        # Prepare scopes as JSON string if provided
        scopes_json = json.dumps(api_key_data.scopes) if api_key_data.scopes else None

        # Create the API key record
        key_record = await crud_api_keys.create(
            db=self.db,
            object={
                "name": api_key_data.name,
                "key_prefix": prefix,
                "hashed_key": hashed_key,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "scopes": scopes_json,
                "expires_at": api_key_data.expires_at,
                "is_active": True,
            },
        )

        return APIKeyResponse(
            id=key_record["id"],
            name=key_record["name"],
            key=full_key,  # Only returned on creation!
            key_prefix=prefix,
            expires_at=key_record.get("expires_at"),
            created_at=key_record["created_at"],
        )

    async def list_api_keys(
        self,
        user_id: int,
        tenant_id: int,
        page: int = 1,
        items_per_page: int = 10,
    ) -> dict[str, Any]:
        """List API keys for a user.

        Parameters
        ----------
        user_id : int
            ID of the user
        tenant_id : int
            ID of the tenant
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict
            Paginated list of API keys (without actual keys)
        """
        return await crud_api_keys.get_multi(
            db=self.db,
            user_id=user_id,
            tenant_id=tenant_id,
            is_deleted=False,
            offset=(page - 1) * items_per_page,
            limit=items_per_page,
            schema_to_select=APIKeyRead,
        )

    async def revoke_api_key(
        self,
        key_id: int,
        user_id: int,
        tenant_id: int,
    ) -> dict[str, str]:
        """Revoke an API key.

        Parameters
        ----------
        key_id : int
            ID of the API key to revoke
        user_id : int
            ID of the user making the request
        tenant_id : int
            ID of the tenant

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If key not found
        ForbiddenException
            If user doesn't own the key
        """
        key = await crud_api_keys.get(db=self.db, id=key_id, is_deleted=False)

        if not key:
            raise ModelNotFoundError("APIKey", key_id)

        if key["user_id"] != user_id or key["tenant_id"] != tenant_id:
            raise ForbiddenException("You don't have permission to revoke this key")

        await crud_api_keys.update(
            db=self.db,
            id=key_id,
            object={"is_active": False},
        )

        return {"message": "API key revoked successfully"}

    async def validate_api_key(
        self,
        api_key: str,
    ) -> dict[str, Any] | None:
        """Validate an API key and return the associated user info.

        Security: Verifies tenant is active to prevent suspended tenant access.

        Parameters
        ----------
        api_key : str
            The API key to validate

        Returns
        -------
        dict | None
            User info if valid, None otherwise
        """
        from sqlalchemy import select

        from ...modules.tenants.models import Tenant
        from .models import APIKey

        hashed = self._hash_key(api_key)

        # JOIN with tenant to check tenant.is_active
        stmt = (
            select(APIKey)
            .join(Tenant, APIKey.tenant_id == Tenant.id)
            .where(
                APIKey.hashed_key == hashed,
                APIKey.is_active.is_(True),
                Tenant.is_active.is_(True),  # Prevent suspended tenant access
            )
        )

        result = await self.db.execute(stmt)
        key = result.scalar_one_or_none()

        if not key:
            return None

        # Check expiration
        if key.expires_at and key.expires_at < datetime.now(UTC):
            return None

        # Update last used timestamp
        await crud_api_keys.update(
            db=self.db,
            id=key.id,
            object={"last_used_at": datetime.now(UTC)},
        )

        # Return user info for authentication
        return {
            "user_id": key.user_id,
            "tenant_id": key.tenant_id,
            "scopes": json.loads(key.scopes) if key.scopes else [],
            "key_id": key.id,
        }


def get_api_key_service(db: AsyncSession) -> APIKeyService:
    """Dependency injection factory for APIKeyService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    APIKeyService
        Service instance with database session
    """
    return APIKeyService(db)


# DI factory registration with service registry
def _register_api_key_service() -> None:
    """Register APIKeyService with the service registry."""
    from src.app.core.service_registry import register_service

    register_service("api_key", lambda db: APIKeyService(db))
