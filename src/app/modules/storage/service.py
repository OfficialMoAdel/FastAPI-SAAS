"""Storage service layer - tenant file operations (transfer) over providers.

Deep module unifying:
- File persistence, MIME verification, and atomic quota enforcement
- Transfers against S3, Azure Blob, and Local providers

Tenant storage configuration, Fernet encryption at rest, credential
validation, and provider resolution live in storage.admin; this module
consumes the narrow ResolvedStorage policy object across that seam.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, BinaryIO
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.clients.storage.local_storage import LocalStorage
from src.app.core.config import settings as app_settings
from src.app.core.db.scoped import execute_scoped
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.modules.storage.admin import ResolvedStorage, StorageAdminService
from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    QuotaExceededError,
    StorageBackendError,
)
from src.app.modules.storage.metrics import storage_metrics
from src.app.modules.storage.models import File
from src.app.modules.storage.schemas import (
    FileRead,
    FileUpdate,
    QuotaResponse,
)
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)


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


# NOTE: StorageAdminService (tenant configuration, credentials, provider cache)
# lives in storage.admin; re-exported here for facade compatibility.


# StorageService: Core File Lifecycle & Operational Facade
# ============================================================================


class StorageService:
    """Deep Storage Facade.

    Encapsulates file uploads, downloads, deletions, quota management,
    and provider orchestration behind a single interface.
    """

    def __init__(self, db: AsyncSession, admin: StorageAdminService | None = None) -> None:
        self.db = db
        self.admin = admin or StorageAdminService(db)

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
        resolved = await self.admin.resolve(tenant_id)

        # Check file size limit
        max_size = resolved.max_file_size_mb * 1024 * 1024
        if size_bytes > max_size:
            raise QuotaExceededError(f"File size {size_bytes} exceeds limit of {resolved.max_file_size_mb}MB")

        # Check client-side claimed file type
        if resolved.allowed_file_types and mime_type not in resolved.allowed_file_types:
            raise FileTypeNotAllowedError(
                f"File type '{mime_type}' is not allowed. Permitted types: {resolved.allowed_file_types}"
            )

        # Magic byte validation to prevent MIME spoofing
        import filetype

        head = await asyncio.to_thread(file.read, 2048)
        if hasattr(file, "seek"):
            await asyncio.to_thread(file.seek, 0)

        kind = filetype.guess(head)
        actual_mime = kind.mime if kind else None

        # R3-1: sniff-gate covers what libmagic can see. Binary claims with
        # no detectable signature are rejected; a known actual MIME that
        # contradicts the claim is rejected unless the tenant allowlist
        # permits it. Text claims (filetype returns None for prose) cannot
        # be sniff-verified without false-positiving legitimate files, so
        # they pass here — compensated at serve time by forced-attachment
        # on every path (API/S3/Azure) plus nosniff on the API download.
        is_binary_claim = mime_type.startswith(("image/", "video/", "audio/", "application/pdf"))
        if is_binary_claim and not actual_mime:
            raise FileTypeNotAllowedError("Invalid file content: missing expected file signature.")

        if actual_mime and actual_mime != mime_type:
            if resolved.allowed_file_types and actual_mime not in resolved.allowed_file_types:
                raise FileTypeNotAllowedError(f"Spoofed file detected. Actual type '{actual_mime}' is not allowed.")

        # Generate unique storage path
        file_uuid = uuid.uuid4()
        raw_extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
        extension = re.sub(r"[^a-zA-Z0-9]", "", raw_extension)[:10]

        base_path = f"tenants/{tenant_id}/files/{file_uuid}"
        storage_path = f"{base_path}.{extension}" if extension else base_path

        # Check quotas in one query
        current_count, current_size = await self._get_quota_stats(tenant_id)
        if current_count >= resolved.max_file_count:
            raise QuotaExceededError(f"File count limit reached ({resolved.max_file_count} files)")

        if current_size + size_bytes > resolved.storage_quota_bytes:
            raise QuotaExceededError(
                f"Storage quota exceeded. Used: {current_size}, Limit: {resolved.storage_quota_bytes}"
            )

        provider = resolved.provider

        # FEATURE_UPLOAD_TXN_SPLIT: end read txn before storage network I/O
        max_file_count = resolved.max_file_count
        storage_quota_bytes = resolved.storage_quota_bytes
        provider_name = resolved.provider_name
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
        query = select(File).where(File.id == file_id)
        if user_id is not None:
            query = query.where(File.user_id == user_id)
        result = await execute_scoped(
            query,
            db=self.db,
            model=File,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
        )
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
        resolved = await self.admin.resolve(tenant_id)
        file_dto = FileRead.model_validate(file, from_attributes=True)

        def stream_factory() -> AsyncIterator[bytes]:
        # P2: file row already resolved above - the stream
            # performs 0 DB RTTs before first byte (previously re-fetched
            # both). Fail-fast bonus: misconfigured storage now 404s here
            # instead of dying mid-stream after 200 headers.
            return self._stream_from_prefetched(file, resolved)

        return Download(file=file_dto, _stream_factory=stream_factory)

    async def _stream_from_prefetched(
        self,
        file: File,
        resolved: ResolvedStorage,
    ) -> AsyncGenerator[bytes, None]:
        """Stream chunks for an already-resolved file row + resolved policy."""
        provider = resolved.provider

        tid = str(file.tenant_id)
        logger.info("file_download_started", file_id=file.id, tenant_id=tid, storage_path=file.storage_path)

        with storage_metrics.measure_download(tid, resolved.provider_name):
            async for chunk in provider.download(file.storage_path):
                yield chunk

        storage_metrics.record_download(
            tenant_id=tid,
            provider=resolved.provider_name,
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
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        resolved = await self.admin.resolve(tenant_id)
        async for chunk in self._stream_from_prefetched(file, resolved):
            yield chunk

    # ------------------------------------------------------------------------
    # Core Entry Point: delete()
    # ------------------------------------------------------------------------

    async def delete(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> None:
        """Delete a file by ID."""
        await self.delete_file(tenant_id, file_id, user_id)

    async def delete_file(self, tenant_id: UUID, file_id: int, user_id: int | None = None) -> None:
        """Storage-first idempotent delete + row cleanup + quota decrement."""
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        resolved = await self.admin.resolve(tenant_id)
        provider = resolved.provider
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

        stmt = delete(File).where(File.id == file_id)
        if user_id is not None:
            stmt = stmt.where(File.user_id == user_id)
        result = await execute_scoped(
            stmt,
            db=self.db,
            model=File,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
        )

        if int(getattr(result, "rowcount", 0) or 0) == 0:
            logger.warning("file_already_deleted_concurrently", file_id=file_id, tenant_id=str(tenant_id))
            return

        await self._decrement_quota_counters(tenant_id, file_size)
        await self.db.commit()

        logger.info("file_deleted", file_id=file_id, tenant_id=str(tenant_id), size_bytes=file_size)
        storage_metrics.record_delete(
            tenant_id=str(tenant_id),
            provider=resolved.provider_name,
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
        query = select(File)
        filters = []
        if user_id:
            filters.append(File.user_id == user_id)
        if mime_type:
            filters.append(File.mime_type == mime_type)
        if filters:
            query = query.where(*filters)

        if not user_id and not mime_type:
            file_count, _ = await self._get_quota_stats(tenant_id)
            total_count = file_count
            query = query.order_by(File.created_at.desc()).offset(offset).limit(limit)
            result = await execute_scoped(
                query,
                db=self.db,
                model=File,
                scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
            )
            files = result.scalars().all()
        else:
            # P2: single RTT via COUNT(*) OVER() over the identical filtered
            # set (audit_logs pattern) instead of count-query + rows-query.
            total_col = func.count().over().label("total")
            windowed = select(File, total_col)
            if filters:
                windowed = windowed.where(*filters)
            windowed = windowed.order_by(File.created_at.desc()).offset(offset).limit(limit)
            result = await execute_scoped(
                windowed,
                db=self.db,
                model=File,
                scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
            )
            rows = result.all()
            files = [row[0] for row in rows]
            if rows:
                total_count = rows[0][1]
            elif offset == 0:
                total_count = 0
            else:
                count_query = select(func.count(File.id))
                if filters:
                    count_query = count_query.where(*filters)
                total_result = await execute_scoped(
                    count_query,
                    db=self.db,
                    model=File,
                    scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
                )
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
        file = await self.get_file_or_raise(tenant_id, file_id, user_id)
        resolved = await self.admin.resolve(tenant_id)
        provider = resolved.provider
        if isinstance(provider, LocalStorage):
            return f"/api/v1/files/{file.id}"
        return await provider.get_url(file.storage_path, expires_in)

    get_signed_url = get_presigned_url

    async def _get_file_count(self, tenant_id: UUID) -> int:
        result = await execute_scoped(
            select(func.count(File.id)),
            db=self.db,
            model=File,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
        )
        return result.scalar() or 0

    async def _get_total_size(self, tenant_id: UUID) -> int:
        result = await execute_scoped(
            select(func.coalesce(func.sum(File.size_bytes), 0)),
            db=self.db,
            model=File,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
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
            .where(File.user_id == from_user_id)
            .values(user_id=to_user_id)
        )
        result = await execute_scoped(
            stmt,
            db=self.db,
            model=File,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="legacy-criterion:File"),
        )
        await self.db.commit()
        count = int(getattr(result, "rowcount", 0) or 0)
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
        resolved = await self.admin.resolve(tenant_id)
        file_count, storage_bytes = await self._get_quota_stats(tenant_id)

        max_count = resolved.max_file_count
        max_bytes = resolved.storage_quota_bytes
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
