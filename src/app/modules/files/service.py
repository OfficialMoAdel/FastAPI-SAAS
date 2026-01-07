"""File service layer.

This module contains the business logic for file operations,
including upload, download, deletion, and quota management.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.files.models import File
from src.app.modules.files.schemas import FileUpdate
from src.app.modules.storage.metrics import storage_metrics
from src.app.modules.storage_settings import (
    StorageSettingsService,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class QuotaExceededError(Exception):
    """Raised when storage quota is exceeded."""

    pass


class FileNotFoundError(Exception):
    """Raised when file is not found."""

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
        user_id: UUID,
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
            raise QuotaExceededError(f"File type {mime_type} not allowed. Allowed: {settings.allowed_file_types}")

        # Check file count quota
        current_count = await self._get_file_count(tenant_id)
        if current_count >= settings.max_file_count:
            raise QuotaExceededError(f"File count limit reached ({settings.max_file_count} files)")

        # Check storage quota
        current_size = await self._get_total_size(tenant_id)
        if current_size + size_bytes > settings.storage_quota_bytes:
            raise QuotaExceededError(
                f"Storage quota exceeded. Used: {current_size}, Limit: {settings.storage_quota_bytes}"
            )

        # Generate unique storage path
        file_uuid = uuid.uuid4()
        extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
        base_path = f"tenants/{tenant_id}/files/{file_uuid}"
        storage_path = f"{base_path}.{extension}" if extension else base_path

        # Get storage provider
        provider = await self._storage_service.get_provider(tenant_id)

        # Upload to storage
        result = await provider.upload(file, storage_path, mime_type)

        if not result.success:
            raise Exception(f"Storage upload failed: {result.error}")

        # Create database record
        try:
            file_record = File(
                tenant_id=tenant_id,
                user_id=user_id,
                original_name=filename,
                storage_path=storage_path,
                mime_type=mime_type,
                size_bytes=size_bytes,
                is_public=False,
            )

            self.db.add(file_record)
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
            FileNotFoundError: If file not found.
        """
        file = await self.get_file(tenant_id, file_id)
        if not file:
            raise FileNotFoundError(f"File {file_id} not found")
        return file

    async def list_files(
        self,
        tenant_id: UUID,
        user_id: UUID | None = None,
        mime_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[File], int]:
        """List files with optional filtering.

        Args:
            tenant_id: The tenant UUID.
            user_id: Optional filter by owner.
            mime_type: Optional filter by MIME type.
            limit: Page size.
            offset: Page offset.

        Returns:
            Tuple of (files, total_count).
        """
        query = select(File).where(File.tenant_id == tenant_id)

        if user_id:
            query = query.where(File.user_id == user_id)
        if mime_type:
            query = query.where(File.mime_type == mime_type)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar() or 0

        # Get paginated items
        query = query.order_by(File.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        files = list(result.scalars().all())

        return files, total

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
            FileNotFoundError: If file not found.
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
            FileNotFoundError: If file not found.
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
            FileNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)
        provider = await self._storage_service.get_provider(tenant_id)
        settings = await self._storage_service.get_settings_or_raise(tenant_id)
        file_size = file.size_bytes
        storage_path = file.storage_path

        # Delete from storage
        await provider.delete(file.storage_path)

        # Delete metadata
        await self.db.delete(file)
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
            FileNotFoundError: If file not found.
        """
        file = await self.get_file_or_raise(tenant_id, file_id)
        provider = await self._storage_service.get_provider(tenant_id)

        return await provider.get_url(file.storage_path, expires_in)

    async def transfer_ownership(
        self,
        tenant_id: UUID,
        from_user_id: UUID,
        to_user_id: UUID,
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
        result = await self.db.execute(
            select(File).where(
                File.tenant_id == tenant_id,
                File.user_id == from_user_id,
            )
        )
        files = result.scalars().all()

        for file in files:
            file.user_id = to_user_id

        await self.db.commit()

        logger.info(f"Transferred {len(files)} files from {from_user_id} to {to_user_id}")
        return len(files)

    async def _get_file_count(self, tenant_id: UUID) -> int:
        """Get total file count for tenant."""
        result = await self.db.execute(select(func.count()).where(File.tenant_id == tenant_id))
        return result.scalar() or 0

    async def _get_total_size(self, tenant_id: UUID) -> int:
        """Get total storage usage in bytes for tenant."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(File.size_bytes), 0)).where(File.tenant_id == tenant_id)
        )
        return result.scalar() or 0


__all__ = [
    "FileService",
    "QuotaExceededError",
    "FileNotFoundError",
]
