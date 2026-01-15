"""Storage Settings service layer.

This module contains the business logic for storage settings management,
including credential encryption, validation, and provider factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.encryption import decrypt_value, encrypt_value
from src.app.core.json_utils import json_dumps, json_loads
from src.app.modules.storage.azure_storage import AzureBlobStorage
from src.app.modules.storage.interfaces import StorageProvider
from src.app.modules.storage.local_storage import LocalStorage
from src.app.modules.storage_settings.models import StorageProviderType, StorageSettings
from src.app.modules.storage_settings.schemas import (
    StorageSettingsCreate,
    StorageSettingsRead,
    StorageSettingsUpdate,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class StorageNotConfiguredError(Exception):
    """Raised when storage settings are not configured for a tenant."""

    pass


class InvalidCredentialsError(Exception):
    """Raised when storage credentials are invalid."""

    pass


class StorageSettingsService:
    """Service for managing tenant storage settings.

    Handles CRUD operations, credential encryption, validation,
    and storage provider instantiation.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize the service.

        Args:
            db: Async database session.
        """
        self.db = db

    async def get_settings(self, tenant_id: int) -> StorageSettings | None:
        """Get storage settings for a tenant.

        Args:
            tenant_id: The tenant ID.

        Returns:
            StorageSettings or None if not configured.
        """
        result = await self.db.execute(select(StorageSettings).where(StorageSettings.tenant_id == tenant_id))
        return result.scalar_one_or_none()

    async def get_settings_or_raise(self, tenant_id: int) -> StorageSettings:
        """Get storage settings or raise if not configured.

        Args:
            tenant_id: The tenant ID.

        Returns:
            StorageSettings.

        Raises:
            StorageNotConfiguredError: If settings not found.
        """
        settings = await self.get_settings(tenant_id)
        if not settings:
            raise StorageNotConfiguredError("Storage not configured. Contact your tenant administrator.")
        return settings

    async def create_settings(
        self,
        tenant_id: int,
        data: StorageSettingsCreate,
    ) -> StorageSettings:
        """Create storage settings for a tenant.

        Validates credentials before saving (per clarification Q3 - validate on save).

        Args:
            tenant_id: The tenant ID.
            data: Storage settings to create.

        Returns:
            Created StorageSettings.

        Raises:
            InvalidCredentialsError: If credential validation fails.
        """
        # Extract and validate credentials
        credentials = self._extract_credentials(data)

        # Validate credentials by testing connection (clarification Q3)
        await self._validate_credentials(data.provider, credentials)

        # Encrypt credentials
        encrypted = encrypt_value(json_dumps(credentials, as_str=True))

        # Create settings
        settings = StorageSettings(
            tenant_id=tenant_id,
            provider=data.provider.value,
            credentials_encrypted=encrypted,
            bucket_name=credentials.get("bucket_name") or credentials.get("container_name"),
            region=credentials.get("region"),
            max_file_size_mb=data.max_file_size_mb,
            allowed_file_types=data.allowed_file_types,
            max_file_count=data.max_file_count,
            storage_quota_bytes=data.storage_quota_bytes,
        )

        self.db.add(settings)
        await self.db.commit()
        await self.db.refresh(settings)

        logger.info(
            "storage_settings_created",
            tenant_id=str(tenant_id),
            provider=data.provider.value,
            max_file_size_mb=data.max_file_size_mb,
            max_file_count=data.max_file_count,
        )
        return settings

    async def update_settings(
        self,
        tenant_id: int,
        data: StorageSettingsUpdate,
    ) -> StorageSettings:
        """Update storage settings for a tenant.

        Re-validates credentials if updated (per clarification Q3).
        Supports key rotation (per clarification Q5).

        Args:
            tenant_id: The tenant ID.
            data: Fields to update.

        Returns:
            Updated StorageSettings.

        Raises:
            StorageNotConfiguredError: If settings not found.
            InvalidCredentialsError: If credential validation fails.
        """
        settings = await self.get_settings_or_raise(tenant_id)

        # Check if credentials are being updated
        new_credentials = self._extract_credentials_optional(data)
        if new_credentials:
            # Validate new credentials
            provider = data.provider or StorageProviderType(settings.provider)
            await self._validate_credentials(provider, new_credentials)

            # Encrypt with current key (handles rotation automatically)
            settings.credentials_encrypted = encrypt_value(json_dumps(new_credentials, as_str=True))

            # Update bucket/container name if changed
            settings.bucket_name = (
                new_credentials.get("bucket_name") or new_credentials.get("container_name") or settings.bucket_name
            )
            settings.region = new_credentials.get("region") or settings.region

        # Update other fields if provided
        if data.provider is not None:
            settings.provider = data.provider.value
        if data.max_file_size_mb is not None:
            settings.max_file_size_mb = data.max_file_size_mb
        if data.allowed_file_types is not None:
            settings.allowed_file_types = data.allowed_file_types
        if data.max_file_count is not None:
            settings.max_file_count = data.max_file_count
        if data.storage_quota_bytes is not None:
            settings.storage_quota_bytes = data.storage_quota_bytes

        await self.db.commit()
        await self.db.refresh(settings)

        logger.info(
            "storage_settings_updated",
            tenant_id=str(tenant_id),
            provider=settings.provider,
            credentials_updated=bool(new_credentials),
        )
        return settings

    async def get_provider(self, tenant_id: int) -> StorageProvider:
        """Get a configured storage provider for a tenant.

        Args:
            tenant_id: The tenant ID.

        Returns:
            Configured StorageProvider instance.

        Raises:
            StorageNotConfiguredError: If settings not found.
        """
        settings = await self.get_settings_or_raise(tenant_id)
        credentials = self._decrypt_credentials(settings)

        return self._create_provider(
            StorageProviderType(settings.provider),
            credentials,
        )

    def to_read_schema(self, settings: StorageSettings) -> StorageSettingsRead:
        """Convert model to read schema with masked credentials.

        Args:
            settings: The StorageSettings model.

        Returns:
            StorageSettingsRead schema.
        """
        return StorageSettingsRead(
            id=settings.id,
            tenant_id=settings.tenant_id,
            provider=StorageProviderType(settings.provider),
            bucket_name=settings.bucket_name,
            region=settings.region,
            max_file_size_mb=settings.max_file_size_mb,
            allowed_file_types=settings.allowed_file_types,
            max_file_count=settings.max_file_count,
            storage_quota_bytes=settings.storage_quota_bytes,
            has_credentials=settings.credentials_encrypted is not None,
            created_at=settings.created_at,
            updated_at=settings.updated_at,
        )

    def _extract_credentials(self, data: StorageSettingsCreate) -> dict:
        """Extract credentials from create schema based on provider.

        Args:
            data: StorageSettingsCreate schema.

        Returns:
            Credentials dictionary.

        Raises:
            ValueError: If credentials missing for provider.
        """
        if data.provider == StorageProviderType.S3:
            if not data.s3_credentials:
                raise ValueError("S3 credentials required for S3 provider")
            return data.s3_credentials.model_dump()

        if data.provider == StorageProviderType.AZURE:
            if not data.azure_credentials:
                raise ValueError("Azure credentials required for Azure provider")
            return data.azure_credentials.model_dump()

        if data.provider == StorageProviderType.LOCAL:
            if not data.local_credentials:
                raise ValueError("Local credentials required for Local provider")
            return data.local_credentials.model_dump()

        raise ValueError(f"Unknown provider: {data.provider}")

    def _extract_credentials_optional(self, data: StorageSettingsUpdate) -> dict | None:
        """Extract credentials from update schema if provided.

        Args:
            data: StorageSettingsUpdate schema.

        Returns:
            Credentials dictionary or None.
        """
        if data.s3_credentials:
            return data.s3_credentials.model_dump()
        if data.azure_credentials:
            return data.azure_credentials.model_dump()
        if data.local_credentials:
            return data.local_credentials.model_dump()
        return None

    def _decrypt_credentials(self, settings: StorageSettings) -> dict:
        """Decrypt stored credentials.

        Supports key rotation (per clarification Q5) via encryption module.

        Args:
            settings: StorageSettings with encrypted credentials.

        Returns:
            Decrypted credentials dictionary.
        """
        if not settings.credentials_encrypted:
            return {}
        decrypted = decrypt_value(settings.credentials_encrypted)
        return json_loads(decrypted)

    async def _validate_credentials(
        self,
        provider: StorageProviderType,
        credentials: dict,
    ) -> None:
        """Validate credentials by testing connection.

        Per clarification Q3: validate credentials on save.

        Args:
            provider: Storage provider type.
            credentials: Credentials dictionary.

        Raises:
            InvalidCredentialsError: If validation fails.
        """
        storage = self._create_provider(provider, credentials)

        try:
            is_valid = await storage.validate_connection()
            if not is_valid:
                raise InvalidCredentialsError(f"Failed to validate {provider.value} credentials")
        except Exception as e:
            logger.error(f"Credential validation failed for {provider.value}: {e}")
            raise InvalidCredentialsError(f"Failed to validate {provider.value} credentials: {e}")
        finally:
            # Cleanup connection
            if hasattr(storage, "close"):
                await storage.close()

    def _create_provider(
        self,
        provider: StorageProviderType,
        credentials: dict,
    ) -> StorageProvider:
        """Create a storage provider instance.

        Args:
            provider: Storage provider type.
            credentials: Credentials dictionary.

        Returns:
            StorageProvider instance.
        """
        if provider == StorageProviderType.S3:
            # S3 would need boto3 implementation
            raise NotImplementedError("S3 provider not yet implemented")

        if provider == StorageProviderType.AZURE:
            return AzureBlobStorage(
                connection_string=credentials["connection_string"],
                container_name=credentials["container_name"],
            )

        if provider == StorageProviderType.LOCAL:
            return LocalStorage(
                base_path=credentials["base_path"],
                base_url=credentials.get("base_url", "/files"),
            )

        raise ValueError(f"Unknown provider: {provider}")


def get_storage_settings_service(
    db: AsyncSession = Depends(async_get_db),
) -> StorageSettingsService:
    """Dependency injection factory for StorageSettingsService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    StorageSettingsService
        Service instance with database session
    """
    return StorageSettingsService(db)


__all__ = [
    "StorageSettingsService",
    "StorageNotConfiguredError",
    "InvalidCredentialsError",
    "get_storage_settings_service",
]
