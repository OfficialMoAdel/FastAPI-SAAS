"""File service layer.

This module contains the business logic for file operations,
including upload, download, deletion, and quota management.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, BinaryIO
from uuid import UUID

import structlog
from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.utils import cache
from src.app.modules.files.models import File
from src.app.modules.files.schemas import FileUpdate
from src.app.modules.storage.metrics import storage_metrics
from src.app.modules.storage_settings import (
    StorageSettingsService,
)
from src.app.modules.tenants.models import Tenant

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class QuotaExceededError(Exception):
    """Raised when storage quota is exceeded."""

    pass


class FileTypeNotAllowedError(ValueError):
    """Raised when the uploaded file's MIME type is not permitted by tenant settings."""

    pass


class FileEntityNotFoundError(Exception):
    """Raised when file entity is not found in database."""

    pass


class FileService:
    """Service for file operations.

    Handles upload, download, deletion, and quota enforcement.
    Implements cleanup on failure (per clarification Q4).
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize the service.

        Args:
            db: Async database session.
        """
        self.db = db
        self._storage_service = StorageSettingsService(db)

    async def upload_file(
        self,
        tenant_id: UUID,
        user_id: int,
        file: BinaryIO,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> File:
        """Upload a file for a tenant.

        Performs quota checks, stores file, and saves metadata.
        Implements cleanup on failure (per clarification Q4).

        Args:
            tenant_id: The tenant UUID.
            user_id: The uploading user's UUID.
            file: File-like object to upload.
            filename: Original filename.
            mime_type: MIME type of the file.
            size_bytes: File size in bytes.

        Returns:
            Created File metadata.

        Raises:
            StorageNotConfiguredError: If storage not configured.
            QuotaExceededError: If quota exceeded.
        """
        # Get storage settings
        settings = await self._storage_service.get_settings_or_raise(tenant_id)

        # Check file size limit
        max_size = settings.max_file_size_mb * 1024 * 1024
        if size_bytes > max_size:
            raise QuotaExceededError(f"File size {size_bytes} exceeds limit of {settings.max_file_size_mb}MB")

        # Check file type
        if settings.allowed_file_types and mime_type not in settings.allowed_file_types:
            raise FileTypeNotAllowedError(
                f"File type '{mime_type}' is not allowed. Permitted types: {settings.allowed_file_types}"
            )

        # Generate unique storage path
        file_uuid = uuid.uuid4()
        extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
        base_path = f"tenants/{tenant_id}/files/{file_uuid}"
        storage_path = f"{base_path}.{extension}" if extension else base_path

        # Check quotas (count and size) in one query
        current_count, current_size = await self._get_quota_stats(tenant_id)

        if current_count >= settings.max_file_count:
            raise QuotaExceededError(f"File count limit reached ({settings.max_file_count} files)")

        if current_size + size_bytes > settings.storage_quota_bytes:
            raise QuotaExceededError(
                f"Storage quota exceeded. Used: {current_size}, Limit: {settings.storage_quota_bytes}"
            )

        # Get storage provider
        provider = await self._storage_service.get_provider(tenant_id)

        # Acquire a distributed per-tenant upload lock to prevent concurrent
        # quota races. Uses Redis SET NX (atomic compare-and-set).
        lock_key = f"upload_lock:{tenant_id}"
        lock_acquired = False
        if cache.client:
            acquired = await cache.client.set(lock_key, "1", nx=True, ex=30)
            if not acquired:
                raise QuotaExceededError("Another upload is currently in progress")
            lock_acquired = True

        # Upload to storage
        result = await provider.upload(file, storage_path, mime_type)

        if not result.success:
            raise Exception(f"Storage upload failed: {result.error}")

        # ... remaining logic ...
        # File model: tenant_id is UUID, user_id is int (FK to user.id)
        # tenant_id is already UUID from API, user_id is already int from User model
        try:
            file_record = File(
                tenant_id=tenant_id,  # UUID - no cast needed
                user_id=user_id,  # int - already correct type
                original_name=filename,
                storage_path=storage_path,
                mime_type=mime_type,
                size_bytes=size_bytes,
                is_public=False,
            )

            self.db.add(file_record)

            # Atomically increment quota counters and strictly enforce hard limit (P0 optimization)
            await self._increment_quota_counters(
                tenant_id,
                size_bytes,
                settings.max_file_count,
                settings.storage_quota_bytes
            )

            await self.db.commit()
            await self.db.refresh(file_record)

            # Log success with structured context
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

            # Record metrics
            storage_metrics.record_upload(
                tenant_id=str(tenant_id),
                provider=settings.provider,
                size_bytes=size_bytes,
                mime_type=mime_type,
                success=True,
            )

            return file_record

        except Exception as e:
            # Cleanup on failure (per clarification Q4)
            logger.error(
                "file_upload_failed",
                tenant_id=str(tenant_id),
                filename=filename,
                error=str(e),
            )
            storage_metrics.record_upload(
                tenant_id=str(tenant_id),
                provider=settings.provider,
                size_bytes=size_bytes,
                success=False,
            )
            await provider.delete(storage_path)
            raise
        finally:
            # Only release the lock if we successfully acquired it.
            # Do NOT delete if we never acquired it — that would release another request's lock.
            if lock_acquired and cache.client:
                await cache.client.delete(lock_key)

    async def get_file(self, tenant_id: UUID, file_id: int) -> File | None:
        """Get file by ID with tenant isolation.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.

        Returns:
            File or None if not found.
        """
        result = await self.db.execute(
            select(File).where(
                File.id == file_id,
                File.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_file_or_raise(self, tenant_id: UUID, file_id: int) -> File:
        """Get file or raise if not found.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.

        Returns:
            File.

        Raises:
            FileEntityNotFoundError: If file not found.
        """
        file = await self.get_file(tenant_id, file_id)
        if not file:
            raise FileEntityNotFoundError(f"File {file_id} not found")
        return file

    async def list_files(
        self,
        tenant_id: UUID,
        user_id: int | None = None,
        mime_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List files with optional filtering."""

        from src.app.modules.files.schemas import FileRead

        # Build query with SQLAlchemy
        query = select(File).where(File.tenant_id == tenant_id)

        if user_id:
            query = query.where(File.user_id == user_id)
        if mime_type:
            query = query.where(File.mime_type == mime_type)

        # GI-1: Use O(1) denormalized counter when no filters are active
        # Otherwise fall back to O(N) count query for filtered results
        if not user_id and not mime_type:
            # O(1) lookup from Tenant.file_count
            file_count, _ = await self._get_quota_stats(tenant_id)
            total_count = file_count
        else:
            # O(N) filtered count requires query
            count_query = select(func.count()).select_from(query.subquery())
            total_result = await self.db.execute(count_query)
            total_count = total_result.scalar() or 0

        # Apply sorting and pagination
        query = query.order_by(File.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.db.execute(query)
        files = result.scalars().all()

        return {
            "data": [FileRead.model_validate(f) for f in files],
            "total_count": total_count,
        }

    async def download_file(
        self,
        tenant_id: UUID,
        file_id: int,
    ) -> AsyncGenerator[bytes, None]:
        """Download file content as async stream.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.

        Yields:
            File content chunks.

        Raises:
            FileEntityNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)
        provider = await self._storage_service.get_provider(tenant_id)
        settings = await self._storage_service.get_settings_or_raise(tenant_id)

        logger.info(
            "file_download_started",
            file_id=file_id,
            tenant_id=str(tenant_id),
            storage_path=file.storage_path,
        )

        with storage_metrics.measure_download(str(tenant_id), settings.provider):
            async for chunk in provider.download(file.storage_path):
                yield chunk

        storage_metrics.record_download(
            tenant_id=str(tenant_id),
            provider=settings.provider,
            size_bytes=file.size_bytes,
            success=True,
        )

    async def update_file(
        self,
        tenant_id: UUID,
        file_id: int,
        data: FileUpdate,
    ) -> File:
        """Update file metadata.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.
            data: Update data.

        Returns:
            Updated File.

        Raises:
            FileEntityNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)

        if data.original_name is not None:
            file.original_name = data.original_name
        if data.is_public is not None:
            file.is_public = data.is_public
        if data.description is not None:
            file.description = data.description

        await self.db.commit()
        await self.db.refresh(file)

        return file

    async def delete_file(self, tenant_id: UUID, file_id: int) -> None:
        """Delete a file from storage and database.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.

        Raises:
            FileEntityNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)
        provider = await self._storage_service.get_provider(tenant_id)
        settings = await self._storage_service.get_settings_or_raise(tenant_id)
        file_size = file.size_bytes
        storage_path = file.storage_path

        # BUG FIX: Delete from storage FIRST to prevent orphaned physical files.
        # Storage deletion (S3) is idempotent, so if DB commit fails, retrying is safe.
        try:
            await provider.delete(storage_path)
        except Exception as e:
            logger.error("storage_delete_failed", storage_path=storage_path, error=str(e))
            raise Exception(f"Failed to delete file from storage: {e}")

        # Execute an explicit targeted DELETE statement and check the rowcount.
        stmt = delete(File).where(File.id == file_id, File.tenant_id == tenant_id)
        result = await self.db.execute(stmt)

        if result.rowcount == 0:
            # Short-circuit if another concurrent request just successfully deleted this file
            logger.warning("file_already_deleted_concurrently", file_id=file_id, tenant_id=str(tenant_id))
            return

        await self._decrement_quota_counters(tenant_id, file_size)
        await self.db.commit()

        logger.info(
            "file_deleted",
            file_id=file_id,
            tenant_id=str(tenant_id),
            storage_path=storage_path,
            size_bytes=file_size,
        )

        storage_metrics.record_delete(
            tenant_id=str(tenant_id),
            provider=settings.provider,
            size_bytes=file_size,
            success=True,
        )

    async def get_signed_url(
        self,
        tenant_id: UUID,
        file_id: int,
        expires_in: int = 3600,
    ) -> str:
        """Get signed URL for file access.

        Args:
            tenant_id: The tenant UUID.
            file_id: The file ID.
            expires_in: URL expiration in seconds.

        Returns:
            Signed URL.

        Raises:
            FileEntityNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)
        provider = await self._storage_service.get_provider(tenant_id)

        url: str = await provider.get_url(file.storage_path, expires_in)
        return url

    async def transfer_ownership(
        self,
        tenant_id: UUID,
        from_user_id: int,
        to_user_id: int,
    ) -> int:
        """Transfer file ownership from one user to another.

        Used when a user is deleted (per clarification Q2).

        Args:
            tenant_id: The tenant UUID.
            from_user_id: User whose files to transfer.
            to_user_id: New owner (typically tenant admin).

        Returns:
            Number of files transferred.
        """
        # OPT-9: Direct bulk UPDATE without loading objects into memory
        from sqlalchemy import update

        stmt = (
            update(File)
            .where(
                File.tenant_id == tenant_id,
                File.user_id == from_user_id,
            )
            .values(user_id=to_user_id)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()

        count = result.rowcount
        logger.info("Transferred files from to", count=count, from_user_id=from_user_id, to_user_id=to_user_id)
        return count

    async def _get_quota_stats(self, tenant_id: UUID) -> tuple[int, int]:
        """Get file count and total size from denormalized tenant counters (O(1)).

        This is a performance optimization (P0) that eliminates the O(N) COUNT + SUM
        query by reading denormalized counters from the tenants table.

        Args:
            tenant_id: Tenant UUID

        Returns:
            Tuple of (file_count, total_size_bytes)
        """
        result = await self.db.execute(
            select(Tenant.file_count, Tenant.storage_usage_bytes).where(Tenant.id == tenant_id)
        )
        row = result.one_or_none()
        if not row:
            return 0, 0
        return row.file_count, row.storage_usage_bytes

    async def _increment_quota_counters(
        self,
        tenant_id: UUID,
        size_bytes: int,
        max_file_count: int | None = None,
        max_storage_bytes: int | None = None,
    ) -> None:
        """Atomically increment tenant storage counters after file upload.

        Uses UPDATE with arithmetic operations. When max limits are provided,
        applies strict WHERE clauses to enforce quotas atomically.

        Args:
            tenant_id: Tenant UUID
            size_bytes: File size to add to storage usage
            max_file_count: Optional maximum allowed files (enforces atomically if provided)
            max_storage_bytes: Optional maximum allowed storage bytes (enforces atomically if provided)

        Raises:
            QuotaExceededError: If the atomic update violates storage quotas
        """
        if max_file_count is not None and max_storage_bytes is not None:
            # Strict atomic enforcement with WHERE-clause guards
            stmt = (
                update(Tenant)
                .where(
                    Tenant.id == tenant_id,
                    Tenant.file_count + 1 <= max_file_count,
                    Tenant.storage_usage_bytes + size_bytes <= max_storage_bytes
                )
                .values(
                    file_count=Tenant.file_count + 1,
                    storage_usage_bytes=Tenant.storage_usage_bytes + size_bytes,
                )
            )
            result = await self.db.execute(stmt)
            if result.rowcount == 0:
                raise QuotaExceededError("Upload failed: strict quota limits exceeded.")
        else:
            # Simple increment without strict enforcement
            stmt = (
                update(Tenant)
                .where(Tenant.id == tenant_id)
                .values(
                    file_count=Tenant.file_count + 1,
                    storage_usage_bytes=Tenant.storage_usage_bytes + size_bytes,
                )
            )
            await self.db.execute(stmt)
        # Note: Commit happens in upload_file after file record is created

    async def _decrement_quota_counters(self, tenant_id: UUID, size_bytes: int) -> None:
        """Atomically decrement tenant storage counters after file deletion.

        Uses UPDATE with arithmetic operations for atomic decrement without race conditions.

        Args:
            tenant_id: Tenant UUID
            size_bytes: File size to subtract from storage usage
        """
        stmt = (
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                file_count=Tenant.file_count - 1,
                storage_usage_bytes=Tenant.storage_usage_bytes - size_bytes,
            )
        )
        await self.db.execute(stmt)
        # Note: Commit happens in delete_file after file record is deleted

    async def _get_file_count(self, tenant_id: UUID) -> int:
        """Get total file count for tenant.

        Deprecated: Use _get_quota_stats instead.
        """
        result = await self.db.execute(select(func.count()).where(File.tenant_id == tenant_id))
        return result.scalar() or 0

    async def _get_total_size(self, tenant_id: UUID) -> int:
        """Get total storage usage in bytes for tenant.

        Deprecated: Use _get_quota_stats instead.
        """
        result = await self.db.execute(
            select(func.coalesce(func.sum(File.size_bytes), 0)).where(File.tenant_id == tenant_id)
        )
        return result.scalar() or 0


def get_file_service(db: AsyncSession = Depends(async_get_db)) -> FileService:
    """Dependency injection factory for FileService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    FileService
        Service instance with database session
    """
    return FileService(db)


__all__ = [
    "FileService",
    "QuotaExceededError",
    "FileTypeNotAllowedError",
    "FileEntityNotFoundError",
    "get_file_service",
]
