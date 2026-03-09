from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.warmer import warmer
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client

logger = get_logger(__name__)
from src.app.core.exceptions.http_exceptions import (
    DuplicateValueException,
    NotFoundException,
)
from src.app.core.security.encryption import get_encryption
from src.app.core.tenancy.context import get_tenant_context  # Needed to get default tenant_id if not passed
from src.app.modules.audit_logs.service import audit_action
from src.app.modules.sso_settings.crud import crud_sso_settings
from src.app.modules.sso_settings.models import SSOSettings
from src.app.modules.sso_settings.schemas import (
    SSOSettingsCreate,
    SSOSettingsCreateInternal,
    SSOSettingsRead,
    SSOSettingsUpdate,
)


class SSOSettingsService:
    """Service for managing SSO provider settings with encryption."""

    @audit_action("sso_settings.create", "sso_settings")
    async def create_settings(
        self, db: AsyncSession, settings: SSOSettingsCreate, user_id: int, tenant_id: UUID | None = None
    ) -> SSOSettingsRead:
        """Create new SSO provider settings.

        Encrypts client_id and client_secret before storage.
        """
        # Check if provider already exists
        if await crud_sso_settings.exists(db=db, provider=settings.provider):
            raise DuplicateValueException(f"Settings for provider '{settings.provider}' already exist")

        encryption = get_encryption()

        # Determine tenant_id if not passed
        if not tenant_id:
            tenant = get_tenant_context()
            if tenant:
                tenant_id = tenant.id

        # Encrypt secrets (returns str, encode to bytes for DB)
        # Note: encrypt_column returns a versioned base64 string
        encrypted_client_id = encryption.encrypt_column(settings.client_id).encode("utf-8")
        encrypted_client_secret = encryption.encrypt_column(settings.client_secret).encode("utf-8")

        settings_internal = SSOSettingsCreateInternal(
            provider=settings.provider,
            client_id=encrypted_client_id,
            client_secret=encrypted_client_secret,
            additional_config=settings.additional_config,
            is_enabled=settings.is_enabled,
        )

        db_obj = SSOSettings(**settings_internal.model_dump())
        # Tenant context handled by middleware/FastCRUD if available

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)

        return SSOSettingsRead.model_validate(db_obj)

    async def get_settings_by_provider(self, db: AsyncSession, provider: str) -> SSOSettingsRead:
        """Get public settings for a provider (no secrets)."""
        settings = await crud_sso_settings.get(db=db, provider=provider)
        if not settings:
            raise NotFoundException(f"Settings for provider '{provider}' not found")
        return SSOSettingsRead.model_validate(settings)

    async def get_decrypted_settings(self, db: AsyncSession, provider: str) -> dict[str, Any]:
        """Get settings with decrypted secrets (Internal Use Only).

        Returns:
            Dict containing client_id, client_secret, and additional_config.
        """
        settings = await crud_sso_settings.get(db=db, provider=provider)
        if not settings:
            raise NotFoundException(f"Settings for provider '{provider}' not found")

        encryption = get_encryption()

        # Decrypt secrets (bytes from DB -> decode to str -> decrypt -> str)
        # encrypt_column returned str, which we encoded to bytes.
        # So: bytes -> decode -> str (encrypted) -> decrypt_column -> str (plaintext)
        try:
            # Handle both ORM object and dictionary (FastCRUD might return dict)
            raw_client_id = settings.client_id if hasattr(settings, "client_id") else settings["client_id"]
            raw_client_secret = (
                settings.client_secret if hasattr(settings, "client_secret") else settings["client_secret"]
            )

            client_id = encryption.decrypt_column(raw_client_id.decode("utf-8"))
            client_secret = encryption.decrypt_column(raw_client_secret.decode("utf-8"))
        except Exception as e:
            # If decryption fails, it might be a key issue or data corruption
            raise ValueError(f"Failed to decrypt credentials for {provider}: {e}") from e

        return {
            "id": settings.id if hasattr(settings, "id") else settings["id"],
            "provider": settings.provider if hasattr(settings, "provider") else settings["provider"],
            "client_id": client_id,
            "client_secret": client_secret,
            "additional_config": (
                settings.additional_config
                if hasattr(settings, "additional_config")
                else settings.get("additional_config")
            )
            or {},
            "is_enabled": settings.is_enabled if hasattr(settings, "is_enabled") else settings["is_enabled"],
            "created_at": settings.created_at if hasattr(settings, "created_at") else settings["created_at"],
            "updated_at": settings.updated_at if hasattr(settings, "updated_at") else settings["updated_at"],
        }

    @audit_action("sso_settings.update", "sso_settings")
    async def update_settings(
        self, db: AsyncSession, provider: str, settings: SSOSettingsUpdate, user_id: int, tenant_id: UUID | None = None
    ) -> SSOSettingsRead:
        """Update SSO settings."""
        current = await crud_sso_settings.get(db=db, provider=provider)
        if not current:
            raise NotFoundException(f"Settings for provider '{provider}' not found")

        # Determine tenant_id if not passed
        if not tenant_id:
            tenant = get_tenant_context()
            if tenant:
                tenant_id = tenant.id

        update_data: dict[str, bytes | dict[str, Any] | bool | None] = {}
        encryption = get_encryption()

        if settings.client_id is not None:
            update_data["client_id"] = encryption.encrypt_column(settings.client_id).encode("utf-8")

        if settings.client_secret is not None:
            update_data["client_secret"] = encryption.encrypt_column(settings.client_secret).encode("utf-8")

        if settings.additional_config is not None:
            update_data["additional_config"] = settings.additional_config

        if settings.is_enabled is not None:
            update_data["is_enabled"] = settings.is_enabled

        if not update_data:
            return SSOSettingsRead.model_validate(current)

        await crud_sso_settings.update(db=db, object=update_data, provider=provider)
        updated = await crud_sso_settings.get(db=db, provider=provider)
        return SSOSettingsRead.model_validate(updated)

    @audit_action("sso_settings.delete", "sso_settings")
    async def delete_settings(
        self, db: AsyncSession, provider: str, user_id: int, tenant_id: UUID | None = None
    ) -> None:
        """Delete SSO settings."""
        # Determine tenant_id if not passed (for audit log)
        if not tenant_id:
            tenant = get_tenant_context()
            if tenant:
                tenant_id = tenant.id

        if not await crud_sso_settings.exists(db=db, provider=provider):
            raise NotFoundException(f"Settings for provider '{provider}' not found")

        await crud_sso_settings.delete(db=db, provider=provider)


def get_sso_settings_service(db: AsyncSession = Depends(async_get_db)) -> "SSOSettingsService":
    """Dependency injection factory for SSOSettingsService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    SSOSettingsService
        Service instance
    """
    return SSOSettingsService()


# Legacy singleton for backward compatibility - will be removed
sso_settings_service = SSOSettingsService()


# ============================================================================
# Cache Warming
# ============================================================================


@warmer.register("sso_settings")
async def warm_sso_settings(session: AsyncSession) -> int:
    """Warm SSO settings cache.

    Fetches active SSO providers and stores them in Redis cache.
    Low-cardinality, high-read data perfect for caching.

    Args:
        session: Database session

    Returns:
        Number of SSO settings cached
    """
    from sqlalchemy import select

    from src.app.core.json_utils import json_dumps

    query = select(SSOSettings).where(SSOSettings.is_enabled)
    result = await session.execute(query)
    settings = result.scalars().all()

    cached_count = 0
    if redis_client:
        try:
            for setting in settings:
                cache_key = f"sso_settings:{setting.provider}"
                setting_data = {
                    "id": setting.id,
                    "provider": setting.provider,
                    "is_enabled": setting.is_enabled,
                    "additional_config": setting.additional_config or {},
                }
                await redis_client.setex(cache_key, 300, json_dumps(setting_data, as_str=True))
                cached_count += 1

            logger.info("Warmed cache for SSO settings", cached_count=cached_count)
        except Exception as e:
            logger.error("Failed to warm SSO settings cache", error=e)
            return 0

    return cached_count
