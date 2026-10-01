"""Storage settings, credentials, and provider resolution.

Settings half of the storage split: tenant settings CRUD, credential
decrypt, the :data:`_PROVIDER_CACHE` pool, and :meth:`StorageAdminService.resolve`,
the one seam transfer crosses (narrow :class:`ResolvedStorage`, never the row).
Transfer lives in :mod:`src.app.modules.storage.service`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.clients.storage.interfaces import StorageProvider
from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import StorageProviderRegistry
from src.app.core.db.scoped import execute_scoped
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.security.encryption import get_encryption
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.modules.storage.errors import InvalidCredentialsError, StorageNotConfiguredError
from src.app.modules.storage.models import StorageSettings
from src.app.modules.storage.schemas import StorageProviderType as SchemaProviderType
from src.app.modules.storage.schemas import StorageSettingsCreate, StorageSettingsRead, StorageSettingsUpdate
from src.app.shared.exceptions import DuplicateError

logger = get_logger(__name__)

# Provider construction belongs to the outbound seam, not to this service.
# ``StorageProviderRegistry`` is the only thing admin asks of it, and it is
# imported at module level on purpose: the registry itself reaches only into
# ``src.app.clients.storage`` (card 16), so there is no cycle to defer.

# B6: provider instance cache. boto3/Azure clients are designed for reuse;
# constructing one per operation (SELECT settings -> Fernet decrypt ->
# boto3.client) costs a fresh connection pool per upload/download/delete.
# Keyed (tenant_id, updated_at): any settings mutation bumps updated_at
# (onupdate), rotating the key so a new provider is built on next use.
# Stale entries age out via TTL. Not tenant-scoped by context — keyed by
# explicit tenant_id argument, so cross-tenant leakage is impossible.
_PROVIDER_CACHE: TTLCache[tuple[UUID, datetime | None], StorageProvider] = TTLCache(maxsize=512, ttl=600)

# Bound for the credential-validation network probe (S3/Azure ``exists``).
# Without it a hung endpoint pins an admin worker indefinitely.
_CREDENTIAL_PROBE_TIMEOUT = 10.0


@dataclass(frozen=True)
class ResolvedStorage:
    """Narrow resolved object crossing the settings -> transfer seam.

    Assembled once per operation by :meth:`StorageAdminService.resolve`.
    Transfer owns quota/metrics/streaming against these scalars and never
    touches the settings row, credentials, or decrypt.
    """

    provider: StorageProvider
    provider_name: str
    max_file_size_mb: int
    allowed_file_types: list[str] | None
    max_file_count: int
    storage_quota_bytes: int


class StorageAdminService:
    """Service for managing tenant storage settings and credential encryption."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_settings(self, tenant_id: UUID) -> StorageSettings | None:
        """Get storage settings for a tenant."""
        result = await execute_scoped(
            select(StorageSettings),
            db=self.db,
            model=StorageSettings,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:StorageSettings"),
        )
        return result.scalar_one_or_none()

    async def get_settings_or_raise(self, tenant_id: UUID) -> StorageSettings:
        """Get storage settings or raise StorageNotConfiguredError."""
        settings = await self.get_settings(tenant_id)
        if not settings:
            raise StorageNotConfiguredError("Storage not configured. Contact your tenant administrator.")
        return settings

    async def create_settings(
        self,
        tenant_id: UUID,
        data: StorageSettingsCreate,
    ) -> StorageSettings:
        """Create storage settings for a tenant, validating credentials beforehand."""
        credentials = self._extract_credentials(data)
        await self._validate_credentials(StorageProviderType(data.provider.value), credentials, tenant_id)

        credentials_json = str(json_dumps(credentials, as_str=True))
        encryption = get_encryption()
        encrypted = encryption.encrypt_credential(credentials_json.encode())

        settings = StorageSettings(
            tenant_id=tenant_id,
            provider=StorageProviderType(data.provider.value).value,
            credentials_encrypted=encrypted,
            bucket_name=credentials.get("bucket_name") or credentials.get("container_name"),
            region=credentials.get("region"),
            max_file_size_mb=data.max_file_size_mb,
            allowed_file_types=data.allowed_file_types,
            max_file_count=data.max_file_count,
            storage_quota_bytes=data.storage_quota_bytes,
        )
        self.db.add(settings)
        try:
            await self.db.commit()
        except IntegrityError as e:
            # Create race: a concurrent request inserted first (the router
            # pre-checks existence, but check-then-insert is not atomic).
            await self.db.rollback()
            raise DuplicateError(
                "storage_settings",
                str(tenant_id),
                message="Storage settings already exist for this tenant. Use PATCH to update.",
            ) from e
        await self.db.refresh(settings)

        logger.info("Storage settings created for tenant", tenant_id=str(tenant_id), provider=data.provider)
        return settings

    async def update_settings(
        self,
        tenant_id: UUID,
        data: StorageSettingsUpdate,
    ) -> StorageSettings:
        """Update storage settings for a tenant."""
        settings = await self.get_settings_or_raise(tenant_id)

        new_credentials = self._extract_credentials_optional(data)
        if data.provider is not None and data.provider.value != settings.provider and not new_credentials:
            raise InvalidCredentialsError(
                f"Credentials required when switching storage provider from {settings.provider} "
                f"to {data.provider.value}"
            )

        if new_credentials:
            provider_type = (
                StorageProviderType(data.provider.value)
                if data.provider is not None
                else StorageProviderType(settings.provider)
            )
            await self._validate_credentials(provider_type, new_credentials, tenant_id)

            credentials_json = str(json_dumps(new_credentials, as_str=True))
            encryption = get_encryption()
            settings.credentials_encrypted = encryption.encrypt_credential(credentials_json.encode())

            settings.bucket_name = (
                new_credentials.get("bucket_name") or new_credentials.get("container_name") or settings.bucket_name
            )
            settings.region = new_credentials.get("region") or settings.region

        if data.provider is not None:
            settings.provider = StorageProviderType(data.provider.value).value
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

        logger.info("Storage settings updated for tenant", tenant_id=str(tenant_id), provider=settings.provider)
        return settings

    async def get_provider(self, tenant_id: UUID) -> StorageProvider:
        """Get a configured StorageProvider instance for a tenant.

        B6: instances are cached per (tenant_id, settings.updated_at) —
        boto3/Azure clients are designed for reuse; a fresh client per
        operation built a new connection pool for every upload/download.
        Any settings mutation bumps updated_at, rotating the cache key.
        """
        settings = await self.get_settings_or_raise(tenant_id)
        return self.get_provider_for_settings(settings)

    def get_provider_for_settings(self, settings: StorageSettings) -> StorageProvider:
        """Return a cached provider for already-fetched settings.

        Callers that already hold the settings row (upload/download/delete)
        use this to avoid the second SELECT that get_provider would issue.
        """
        cache_key = (settings.tenant_id, settings.updated_at)
        cached = _PROVIDER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        credentials = self._decrypt_credentials(settings)
        provider = self._create_provider(StorageProviderType(settings.provider), credentials)
        _PROVIDER_CACHE[cache_key] = provider
        return provider

    def to_read_schema(self, settings: StorageSettings) -> StorageSettingsRead:
        """Convert model to read schema with masked credentials."""
        return StorageSettingsRead(
            id=settings.id,
            tenant_id=settings.tenant_id,
            provider=SchemaProviderType(settings.provider),
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

    def _extract_credentials(self, data: StorageSettingsCreate) -> dict[str, Any]:
        if data.provider == StorageProviderType.S3:
            if not data.s3_credentials:
                raise InvalidCredentialsError("S3 credentials required for S3 provider")
            return data.s3_credentials.model_dump()
        if data.provider == StorageProviderType.AZURE:
            if not data.azure_credentials:
                raise InvalidCredentialsError("Azure credentials required for Azure provider")
            return data.azure_credentials.model_dump()
        if data.provider == StorageProviderType.LOCAL:
            if not data.local_credentials:
                raise InvalidCredentialsError("Local credentials required for Local provider")
            return data.local_credentials.model_dump()
        raise ValueError(f"Unknown provider: {data.provider}")

    def _extract_credentials_optional(self, data: StorageSettingsUpdate) -> dict[str, Any] | None:
        if data.s3_credentials:
            return data.s3_credentials.model_dump()
        if data.azure_credentials:
            return data.azure_credentials.model_dump()
        if data.local_credentials:
            return data.local_credentials.model_dump()
        return None

    def _decrypt_credentials(self, settings: StorageSettings) -> dict[str, str]:
        if not settings.credentials_encrypted:
            return {}
        encryption = get_encryption()
        decrypted = encryption.decrypt_credential(settings.credentials_encrypted).decode()
        result: dict[str, str] = json_loads(decrypted)
        return result

    async def _validate_credentials(
        self,
        provider: StorageProviderType,
        credentials: dict[str, Any],
        tenant_id: UUID | None = None,
    ) -> None:
        """Probe credentials, then discard the provider.

        Construction is inside the ``try`` on purpose: the registry raises
        here for a missing required key and for an absent ``boto3`` — which
        is a declared dependency (dxub.9, DECIDED: DECLARE) but still absent
        if the deployment was built from a partial subset — and those are
        validation outcomes too. With the call outside, they escaped
        unlogged and the router's ``except ValueError`` rewrote them to
        "Invalid storage settings payload." — an operator could not tell
        "install boto3" from "pick another backend". The actionable text now
        reaches the log, which is where a client must not see it (see
        ``modules/storage/errors.py``: public messages are deliberately
        fixed, so provider internals never leak).
        """
        storage: StorageProvider | None = None
        try:
            storage = self._create_provider(provider, credentials)
            if provider == StorageProviderType.LOCAL:
                # P2-5: writability probe — exists() cannot catch read-only
                # mounts, and LocalStorage.validate_connection() had no callers.
                probe = getattr(storage, "validate_connection", None)
                if probe is None or not await probe():
                    raise InvalidCredentialsError(
                        f"Local storage path is not writable: {credentials.get('base_path', '?')}"
                    )
            else:
                # Bound the network probe: without a timeout a hung S3/Azure
                # endpoint pins an admin worker indefinitely. TimeoutError is
                # an Exception subclass, so the handler below maps it to
                # InvalidCredentialsError like any other probe failure.
                await asyncio.wait_for(
                    storage.exists("__connection_test__"),
                    timeout=_CREDENTIAL_PROBE_TIMEOUT,
                )
        except Exception as e:
            logger.error(
                "Credential validation failed",
                provider=provider.value,
                tenant_id=str(tenant_id) if tenant_id else None,
                error=str(e),
            )
            raise InvalidCredentialsError(f"Failed to validate {provider.value} credentials: {e}") from e
        finally:
            if storage is not None and hasattr(storage, "close"):
                await storage.close()

    def _create_provider(
        self,
        provider: StorageProviderType,
        credentials: dict[str, Any],
    ) -> StorageProvider:
        return StorageProviderRegistry.create(provider, credentials)

    async def resolve(self, tenant_id: UUID) -> ResolvedStorage:
        """Assemble everything transfer needs: provider + policy scalars.

        One seam between settings/credentials and transfer: callers hold a
        ``ResolvedStorage`` and never touch the settings row or decrypt.
        """
        settings = await self.get_settings_or_raise(tenant_id)
        return ResolvedStorage(
            provider=self.get_provider_for_settings(settings),
            provider_name=settings.provider,
            max_file_size_mb=settings.max_file_size_mb,
            allowed_file_types=settings.allowed_file_types,
            max_file_count=settings.max_file_count,
            storage_quota_bytes=settings.storage_quota_bytes,
        )


async def close_cached_providers() -> int:
    """Close and drop every provider held in ``_PROVIDER_CACHE``.

    Lifespan teardown. The cache holds up to 512 providers for 600s, and
    S3/Azure providers own an HTTP client; without this the process exits
    with those clients still open, and a redeploy inside the TTL strands
    their sockets. LocalStorage answers the same ``close()`` as a no-op, so
    no ``hasattr`` probe is needed.

    Returns the number of providers closed. Failures are logged per
    provider and never abort the drain — one bad provider must not strand
    the rest.
    """
    cached = list(_PROVIDER_CACHE.items())
    _PROVIDER_CACHE.clear()
    closed = 0
    for (tenant_id, _updated_at), provider in cached:
        try:
            await provider.close()
            closed += 1
        except Exception as e:  # noqa: BLE001 - teardown must not mask shutdown
            get_logger(__name__).warning("Failed to close cached storage provider", tenant=str(tenant_id), error=str(e))
    if closed:
        get_logger(__name__).info("Closed cached storage providers", count=closed)
    return closed


__all__ = ["ResolvedStorage", "StorageAdminService", "close_cached_providers"]
