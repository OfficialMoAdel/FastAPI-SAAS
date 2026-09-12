"""Storage service layer - encapsulates file operations and tenant storage settings.

Deep module unifying:
- File persistence, MIME verification, and atomic quota enforcement
- Tenant storage settings, Fernet encryption at rest, and credential validation
- Storage provider resolution (S3, Azure Blob, Local)
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO
from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings as app_settings
from src.app.core.db.tenant_crud import tenant_scope
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.security.encryption import get_encryption
from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    InvalidCredentialsError,
    QuotaExceededError,
    StorageBackendError,
    StorageNotConfiguredError,
)
from src.app.modules.storage.interfaces import StorageProvider
from src.app.modules.storage.local_storage import LocalStorage
from src.app.modules.storage.metrics import storage_metrics
from src.app.modules.storage.models import File, StorageProviderType, StorageSettings
from src.app.modules.storage.schemas import (
    FileRead,
    FileUpdate,
    QuotaResponse,
    StorageSettingsCreate,
    StorageSettingsRead,
    StorageSettingsUpdate,
)
from src.app.modules.storage.schemas import (
    StorageProviderType as SchemaProviderType,
)
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

# B6: provider instance cache. boto3/Azure clients are designed for reuse;
# constructing one per operation (SELECT settings -> Fernet decrypt ->
# boto3.client) costs a fresh connection pool per upload/download/delete.
# Keyed (tenant_id, updated_at): any settings mutation bumps updated_at
# (onupdate), rotating the key so a new provider is built on next use.
# Stale entries age out via TTL. Not tenant-scoped by context — keyed by
# explicit tenant_id argument, so cross-tenant leakage is impossible.
_PROVIDER_CACHE: TTLCache[tuple[UUID, datetime | None], StorageProvider] = TTLCache(
    maxsize=512, ttl=600
)


# ============================================================================
# Value Objects / Continuations
# ============================================================================


@dataclass(frozen=True)
class Download:
    """Download value object wrapping stored file metadata and chunk stream."""

    file: FileRead
    _stream_factory: Callable[[], AsyncIterator[bytes]]

    def iter_bytes(self) -> AsyncIterator[bytes]:
        """Iterate over file bytes in chunks (framework-free)."""
        return self._stream_factory()


class StoredObject:
    """Continuation object returned by put() and get()."""

    def __init__(self, service: StorageService, file_record: File) -> None:
        self._service = service
        self._file = file_record
        self.meta = FileRead.model_validate(file_record, from_attributes=True)

    @property
    def id(self) -> int:
        return self._file.id

    @property
    def tenant_id(self) -> UUID:
        return self._file.tenant_id

    def stream(self) -> AsyncIterator[bytes]:
        """Chunked download stream."""
        return self._service.download_file(self._file.tenant_id, self._file.id)

    async def url(self, expires_in: int = 3600) -> str:
        """Presigned or direct URL for file access."""
        return await self._service.get_presigned_url(self._file.tenant_id, self._file.id, expires_in)

    async def remove(self) -> None:
        """Delete remote physical file + DB row + decrement quota."""
        await self._service.delete_file(self._file.tenant_id, self._file.id)


# ============================================================================
# StorageAdminService: Tenant Storage Configuration
# ============================================================================


class StorageAdminService:
    """Service for managing tenant storage settings and credential encryption."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_settings(self, tenant_id: UUID) -> StorageSettings | None:
        """Get storage settings for a tenant."""
        result = await self.db.execute(select(StorageSettings).where(tenant_scope(StorageSettings, tenant_id)))
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
        await self._validate_credentials(StorageProviderType(data.provider.value), credentials)

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
        await self.db.commit()
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
            await self._validate_credentials(provider_type, new_credentials)

            credentials_json = str(json_dumps(new_credentials, as_str=True))
            encryption = get_encryption()
            settings.credentials_encrypted = encryption.encrypt_credential(credentials_json.encode())

            settings.bucket_name = (
                new_credentials.get("bucket_name")
                or new_credentials.get("container_name")
                or settings.bucket_name
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
    ) -> None:
        storage = self._create_provider(provider, credentials)
        try:
            if provider == StorageProviderType.LOCAL:
                # P2-5: writability probe — exists() cannot catch read-only
                # mounts, and LocalStorage.validate_connection() had no callers.
                probe = getattr(storage, "validate_connection", None)
                if probe is None or not await probe():
                    raise InvalidCredentialsError(
                        f"Local storage path is not writable: {credentials.get('base_path', '?')}"
                    )
            else:
                await storage.exists("__connection_test__")
        except Exception as e:
            logger.error("Credential validation failed", provider=provider.value, error=str(e))
            raise InvalidCredentialsError(f"Failed to validate {provider.value} credentials: {e}") from e
        finally:
            if hasattr(storage, "close"):
                await storage.close()

    def _create_provider(
        self,
        provider: StorageProviderType,
        credentials: dict[str, Any],
    ) -> StorageProvider:
        from src.app.clients.storage.registry import StorageProviderRegistry

        return StorageProviderRegistry.create(provider, credentials)


# ============================================================================
# StorageService: Core File Lifecycle & Operational Facade
# ============================================================================


class StorageService:
    """Deep Storage Facade.

    Encapsulates file uploads, downloads, deletions, quota management,
    and provider orchestration behind a single interface.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.admin = StorageAdminService(db)
        self._storage_service = self.admin

    # ------------------------------------------------------------------------
    # Core Entry Point: put() / upload_file()
    # ------------------------------------------------------------------------

    async def put(
        self,
        tenant_id: UUID,
        user_id: int | None,
        file: BinaryIO,
        filename: str,
        claimed_mime: str | None = None,
        description: str | None = None,
        is_public: bool = False,
    ) -> StoredObject:
        """Store a file in one atomic operation."""
        size_bytes = 0
        if hasattr(file, "seek") and hasattr(file, "tell"):
            await asyncio.to_thread(file.seek, 0, 2)
            size_bytes = await asyncio.to_thread(file.tell)
            await asyncio.to_thread(file.seek, 0)

        record = await self.upload_file(
            tenant_id=tenant_id,
            user_id=user_id,
            file=file,
            filename=filename,
            size_bytes=size_bytes,
            mime_type=claimed_mime or "application/octet-stream",
            description=description,
            is_public=is_public,
        )
        return StoredObject(self, record)

    async def upload_file(
        self,
        tenant_id: UUID,
        user_id: int | None,
        file: BinaryIO,
        filename: str,
        size_bytes: int,
        mime_type: str,
        description: str | None = None,
        is_public: bool = False,
    ) -> File:
        """Upload a file with full validation, quota, and rollback compensation."""
        storage_svc = getattr(self, "_storage_service", self.admin)
        settings = await storage_svc.get_settings_or_raise(tenant_id)

        # Check file size limit
        max_size = settings.max_file_size_mb * 1024 * 1024
        if size_bytes > max_size:
            raise QuotaExceededError(f"File size {size_bytes} exceeds limit of {settings.max_file_size_mb}MB")

        # Check client-side claimed file type
        if settings.allowed_file_types and mime_type not in settings.allowed_file_types:
            raise FileTypeNotAllowedError(
                f"File type '{mime_type}' is not allowed. Permitted types: {settings.allowed_file_types}"
            )

        # Magic byte validation to prevent MIME spoofing
        import filetype

        head = await asyncio.to_thread(file.read, 2048)
        if hasattr(file, "seek"):
            await asyncio.to_thread(file.seek, 0)

        kind = filetype.guess(head)
        actual_mime = kind.mime if kind else None

        is_binary_claim = mime_type.startswith(("image/", "video/", "audio/", "application/pdf"))
        if is_binary_claim and not actual_mime:
            raise FileTypeNotAllowedError("Invalid file content: missing expected file signature.")

        if actual_mime and actual_mime != mime_type:
            if settings.allowed_file_types and actual_mime not in settings.allowed_file_types:
                raise FileTypeNotAllowedError(f"Spoofed file detected. Actual type '{actual_mime}' is not allowed.")

        # Generate unique storage path
        file_uuid = uuid.uuid4()
        raw_extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
        extension = re.sub(r"[^a-zA-Z0-9]", "", raw_extension)[:10]

        base_path = f"tenants/{tenant_id}/files/{file_uuid}"
        storage_path = f"{base_path}.{extension}" if extension else base_path

        # Check quotas in one query
        current_count, current_size = await self._get_quota_stats(tenant_id)
        if current_count >= settings.max_file_count:
            raise QuotaExceededError(f"File count limit reached ({settings.max_file_count} files)")

        if current_size + size_bytes > settings.storage_quota_bytes:
            raise QuotaExceededError(
                f"Storage quota exceeded. Used: {current_size}, Limit: {settings.storage_quota_bytes}"
            )

        provider = storage_svc.get_provider_for_settings(settings)

        # FEATURE_UPLOAD_TXN_SPLIT: end read txn before storage network I/O
        max_file_count = settings.max_file_count
        storage_quota_bytes = settings.storage_quota_bytes
        provider_name = settings.provider
        if app_settings.performance.FEATURE_UPLOAD_TXN_SPLIT:
            await self.db.rollback()

        # Upload to physical storage provider
        try:
            result = await provider.upload(file, storage_path, mime_type)
            if not result.success:
                raise StorageBackendError(f"Storage upload failed: {result.error}")

            file_record = File(
                tenant_id=tenant_id,
                user_id=user_id,
                original_name=filename,
                storage_path=storage_path,
                mime_type=mime_type,
                size_bytes=size_bytes,
                is_public=is_public,
                description=description,
            )
            self.db.add(file_record)

            # Atomically increment quota counters
            await self._increment_quota_counters(tenant_id, size_bytes, max_file_count, storage_quota_bytes)
            await self.db.commit()
            await self.db.refresh(file_record)

            logger.info(
                "file_uploaded",
                file_id=file_record.id,
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                filename=filename,
                storage_path=storage_path,
                size_bytes=size_bytes,
                mime_type=mime_type,
            )

            storage_metrics.record_upload(
                tenant_id=str(tenant_id),
                provider=provider_name,
                size_bytes=size_bytes,
                mime_type=mime_type,
                success=True,
            )
            return file_record

        except Exception as e:
            logger.error("file_upload_failed", tenant_id=str(tenant_id), filename=filename, error=str(e))
            storage_metrics.record_upload(
                tenant_id=str(tenant_id),
                provider=provider_name,
                size_bytes=size_bytes,
                success=False,
            )
            # Compensation: delete physical file if DB commit or upload failed
            try:
                await provider.delete(storage_path)
            except Exception as cleanup_err:
                logger.error("upload_cleanup_failed", storage_path=storage_path, error=str(cleanup_err))
            raise

    # ------------------------------------------------------------------------
    # Core Entry Point: get() / download()
    # ------------------------------------------------------------------------

    async def get(
        self,
        tenant_id: UUID,
        file_id: int,
        *,
        owner_id: int | None = None,
    ) -> StoredObject:
        """Get a stored object handle by ID."""
        file = await self.get_file_or_raise(tenant_id, file_id, owner_id)
        return StoredObject(self, file)

    async def get_file(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> File | None:
        """Get file record by ID with tenant isolation."""
        query = select(File).where(File.id == file_id, tenant_scope(File, tenant_id))
        if user_id is not None:
            query = query.where(File.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_file_or_raise(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> File:
        """Get file record or raise FileEntityNotFoundError."""
        file = await self.get_file(tenant_id, file_id, user_id)
        if not file:
            raise FileEntityNotFoundError(f"File {file_id} not found")
        return file

    async def download(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> Download:
        """Resolve a stored file for streaming download."""
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        storage_svc = getattr(self, "_storage_service", self.admin)
        settings = await storage_svc.get_settings_or_raise(tenant_id)
        file_dto = FileRead.model_validate(file, from_attributes=True)

        def stream_factory() -> AsyncIterator[bytes]:
            # P2: file row + settings already resolved above — the stream
            # performs 0 DB RTTs before first byte (previously re-fetched
            # both). Fail-fast bonus: misconfigured storage now 404s here
            # instead of dying mid-stream after 200 headers.
            return self._stream_from_prefetched(file, settings)

        return Download(file=file_dto, _stream_factory=stream_factory)

    async def _stream_from_prefetched(
        self,
        file: File,
        settings: StorageSettings,
    ) -> AsyncGenerator[bytes, None]:
        """Stream chunks for an already-resolved file row + settings."""
        storage_svc = getattr(self, "_storage_service", self.admin)
        provider = storage_svc.get_provider_for_settings(settings)

        tid = str(file.tenant_id)
        logger.info("file_download_started", file_id=file.id, tenant_id=tid, storage_path=file.storage_path)

        with storage_metrics.measure_download(tid, settings.provider):
            async for chunk in provider.download(file.storage_path):
                yield chunk

        storage_metrics.record_download(
            tenant_id=tid,
            provider=settings.provider,
            size_bytes=file.size_bytes,
            success=True,
        )

    async def download_file(
        self,
        tenant_id: UUID,
        file_id: int,
        user_id: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Stream file chunks from physical storage provider."""
        storage_svc = getattr(self, "_storage_service", self.admin)
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        settings = await storage_svc.get_settings_or_raise(tenant_id)
        async for chunk in self._stream_from_prefetched(file, settings):
            yield chunk

    # ------------------------------------------------------------------------
    # Core Entry Point: delete()
    # ------------------------------------------------------------------------

    async def delete(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> None:
        """Delete a file by ID."""
        await self.delete_file(tenant_id, file_id, user_id)

    async def delete_file(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> None:
        """Storage-first idempotent delete + row cleanup + quota decrement."""
        storage_svc = getattr(self, "_storage_service", self.admin)
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        settings = await storage_svc.get_settings_or_raise(tenant_id)
        provider = storage_svc.get_provider_for_settings(settings)
        file_size = file.size_bytes
        storage_path = file.storage_path

        try:
            delete_res = await provider.delete(storage_path)
            if hasattr(delete_res, "success") and not delete_res.success:
                raise StorageBackendError(f"Failed to delete file from storage: {delete_res.error}")
        except StorageBackendError:
            raise
        except Exception as e:
            logger.error("storage_delete_failed", storage_path=storage_path, error=str(e))
            raise StorageBackendError(f"Failed to delete file from storage: {e}") from e

        stmt = delete(File).where(File.id == file_id, tenant_scope(File, tenant_id))
        if user_id is not None:
            stmt = stmt.where(File.user_id == user_id)
        result = await self.db.execute(stmt)

        if result.rowcount == 0:
            logger.warning("file_already_deleted_concurrently", file_id=file_id, tenant_id=str(tenant_id))
            return

        await self._decrement_quota_counters(tenant_id, file_size)
        await self.db.commit()

        logger.info("file_deleted", file_id=file_id, tenant_id=str(tenant_id), size_bytes=file_size)
        storage_metrics.record_delete(
            tenant_id=str(tenant_id),
            provider=settings.provider,
            size_bytes=file_size,
            success=True,
        )

    # ------------------------------------------------------------------------
    # Queries & Utility Operations
    # ------------------------------------------------------------------------

    async def list_files(
        self,
        tenant_id: UUID,
        user_id: int | None = None,
        mime_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List files for a tenant with pagination."""
        query = select(File).where(tenant_scope(File, tenant_id))
        if user_id:
            query = query.where(File.user_id == user_id)
        if mime_type:
            query = query.where(File.mime_type == mime_type)

        if not user_id and not mime_type:
            file_count, _ = await self._get_quota_stats(tenant_id)
            total_count = file_count
            query = query.order_by(File.created_at.desc()).offset(offset).limit(limit)
            result = await self.db.execute(query)
            files = result.scalars().all()
        else:
            # P2: single RTT via COUNT(*) OVER() over the identical filtered
            # set (audit_logs pattern) instead of count-query + rows-query.
            total_col = func.count().over().label("total")
            windowed = select(File, total_col).where(tenant_scope(File, tenant_id))
            if user_id:
                windowed = windowed.where(File.user_id == user_id)
            if mime_type:
                windowed = windowed.where(File.mime_type == mime_type)
            windowed = windowed.order_by(File.created_at.desc()).offset(offset).limit(limit)
            result = await self.db.execute(windowed)
            rows = result.all()
            files = [row[0] for row in rows]
            if rows:
                total_count = rows[0][1]
            elif offset == 0:
                total_count = 0
            else:
                count_query = select(func.count()).select_from(query.subquery())
                total_result = await self.db.execute(count_query)
                total_count = total_result.scalar() or 0

        items_list = [FileRead.model_validate(f, from_attributes=True) for f in files]

        return {
            "items": items_list,
            "data": items_list,
            "total": total_count,
            "total_count": total_count,
            "page": (offset // limit) + 1 if limit > 0 else 1,
            "per_page": limit,
            "pages": (total_count + limit - 1) // limit if limit > 0 else 1,
        }

    async def update_file(
        self,
        tenant_id: UUID,
        file_id: int,
        data: FileUpdate,
        user_id: int | None = None,
    ) -> File:
        """Update file metadata."""
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        if data.original_name is not None:
            file.original_name = data.original_name
        if data.is_public is not None:
            file.is_public = data.is_public
        if data.description is not None:
            file.description = data.description

        await self.db.commit()
        await self.db.refresh(file)
        return file

    async def get_presigned_url(
        self,
        tenant_id: UUID,
        file_id: int,
        expires_in: int = 3600,
        user_id: int | None = None,
    ) -> str:
        """Generate a presigned access URL.

        P1-10: the local provider cannot mint bearer-capable expiring URLs —
        its direct ``{base_url}/{path}`` link neither expires nor resolves to
        a serving endpoint. For local storage return the authenticated in-API
        download path instead (``/api`` + ``/v1`` + files router prefix): it
        requires FILES_READ, so the URL grants nothing to unauthenticated
        holders — no indefinite-share bearer token.

        P1 FIX (code-review): clamp expires_in to 60..3600 in the service
        (not just the router edge) so in-process callers cannot mint
        long-lived bearer URLs past the HTTP validation.
        """
        expires_in = max(60, min(int(expires_in), 3600))
        storage_svc = getattr(self, "_storage_service", self.admin)
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        settings = await storage_svc.get_settings_or_raise(tenant_id)
        provider = storage_svc.get_provider_for_settings(settings)
        if isinstance(provider, LocalStorage):
            return f"/api/v1/files/{file.id}"
        return await provider.get_url(file.storage_path, expires_in)

    get_signed_url = get_presigned_url

    async def _get_file_count(self, tenant_id: UUID) -> int:
        result = await self.db.execute(select(func.count(File.id)).where(tenant_scope(File, tenant_id)))
        return result.scalar() or 0

    async def _get_total_size(self, tenant_id: UUID) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.sum(File.size_bytes), 0)).where(tenant_scope(File, tenant_id))
        )
        return result.scalar() or 0

    async def transfer_ownership(
        self,
        tenant_id: UUID,
        from_user_id: int,
        to_user_id: int,
    ) -> int:
        """Transfer file ownership between users."""
        stmt = (
            update(File)
            .where(
                tenant_scope(File, tenant_id),
                File.user_id == from_user_id,
            )
            .values(user_id=to_user_id)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        count = result.rowcount
        logger.info("Transferred files ownership", count=count, from_user_id=from_user_id, to_user_id=to_user_id)
        return count

    async def _get_quota_stats(self, tenant_id: UUID) -> tuple[int, int]:
        """Get file count and total size from denormalized tenant counters (O(1))."""
        result = await self.db.execute(
            select(Tenant.file_count, Tenant.storage_usage_bytes).where(Tenant.id == tenant_id)
        )
        row = result.one_or_none()
        if not row:
            return 0, 0
        return row.file_count or 0, row.storage_usage_bytes or 0

    async def get_usage(self, tenant_id: UUID) -> QuotaResponse:
        """Get storage usage statistics for a tenant."""
        storage_svc = getattr(self, "_storage_service", self.admin)
        settings = await storage_svc.get_settings_or_raise(tenant_id)
        file_count, storage_bytes = await self._get_quota_stats(tenant_id)

        max_count = settings.max_file_count
        max_bytes = settings.storage_quota_bytes
        pct = (storage_bytes / max_bytes * 100.0) if max_bytes > 0 else 0.0

        return QuotaResponse(
            used=storage_bytes,
            limit=max_bytes,
            remaining=max(0, max_bytes - storage_bytes),
            file_count=file_count,
            max_file_count=max_count,
            storage_usage_bytes=storage_bytes,
            storage_quota_bytes=max_bytes,
            usage_percentage=round(pct, 2),
            files_remaining=max(0, max_count - file_count),
            bytes_remaining=max(0, max_bytes - storage_bytes),
        )

    async def _increment_quota_counters(
        self,
        tenant_id: UUID,
        size_bytes: int,
        max_file_count: int | None = None,
        max_storage_bytes: int | None = None,
    ) -> None:
        """Atomically increment tenant storage counters with WHERE-guarded quota enforcement."""
        if max_file_count is not None and max_storage_bytes is not None:
            stmt = (
                update(Tenant)
                .where(
                    Tenant.id == tenant_id,
                    func.coalesce(Tenant.file_count, 0) + 1 <= max_file_count,
                    func.coalesce(Tenant.storage_usage_bytes, 0) + size_bytes <= max_storage_bytes,
                )
                .values(
                    file_count=func.coalesce(Tenant.file_count, 0) + 1,
                    storage_usage_bytes=func.coalesce(Tenant.storage_usage_bytes, 0) + size_bytes,
                )
                .execution_options(synchronize_session=False)
            )
            result = await self.db.execute(stmt)
            if result.rowcount == 0:
                raise QuotaExceededError("Upload failed: strict quota limits exceeded.")
        else:
            stmt = (
                update(Tenant)
                .where(Tenant.id == tenant_id)
                .values(
                    file_count=func.coalesce(Tenant.file_count, 0) + 1,
                    storage_usage_bytes=func.coalesce(Tenant.storage_usage_bytes, 0) + size_bytes,
                )
            )
            await self.db.execute(stmt)

    async def _decrement_quota_counters(self, tenant_id: UUID, size_bytes: int) -> None:
        """Atomically decrement tenant storage counters, flooring at 0."""
        stmt = (
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                file_count=func.greatest(0, func.coalesce(Tenant.file_count, 0) - 1),
                storage_usage_bytes=func.greatest(0, func.coalesce(Tenant.storage_usage_bytes, 0) - size_bytes),
            )
        )
        await self.db.execute(stmt)


# ============================================================================
# Aliases & Dependency Injection
# ============================================================================

# Canonical and backward-compatible aliases
Storage = StorageService
StorageAdmin = StorageAdminService
FileService = StorageService
StorageSettingsService = StorageAdminService


# Q-M4: DI factories (get_storage, get_storage_admin, get_file_service,
# get_storage_settings_service) live in routers/files.py and
# routers/settings.py — service files stay Depends-free.


__all__ = [
    "Storage",
    "StorageService",
    "StorageAdmin",
    "StorageAdminService",
    "StoredObject",
    "Download",
    "FileService",
    "StorageSettingsService",
]
