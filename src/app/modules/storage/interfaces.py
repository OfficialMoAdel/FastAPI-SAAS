"""Storage provider interfaces and abstractions.

This module defines the abstract interface for all storage providers,
ensuring consistent API across S3, Azure Blob, Google Cloud Storage,
and local file system implementations.

Usage:
    from src.app.modules.storage import StorageProvider, StorageResult

    class MyStorage(StorageProvider):
        async def upload(self, file, path, mime_type):
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import BinaryIO


@dataclass
class StorageResult:
    """Result of a storage operation.

    Attributes:
        success: Whether the operation succeeded.
        path: Path to the file in storage.
        size: Size of the file in bytes (optional).
        error: Error message if operation failed (optional).
    """

    success: bool
    path: str
    size: int | None = None
    error: str | None = None


class StorageProvider(ABC):
    """Abstract interface for storage providers.

    All storage implementations (S3, Azure, Local) must implement
    this interface to ensure consistent API across providers.

    Example:
        class S3Storage(StorageProvider):
            async def upload(self, file, path, mime_type):
                # S3-specific implementation
                ...
    """

    @abstractmethod
    async def upload(
        self,
        file: BinaryIO,
        path: str,
        mime_type: str,
    ) -> StorageResult:
        """Upload a file to storage.

        Args:
            file: File-like object to upload.
            path: Target path in storage.
            mime_type: MIME type of the file.

        Returns:
            StorageResult with success status and details.
        """
        pass

    @abstractmethod
    async def download(self, path: str) -> AsyncGenerator[bytes, None]:
        """Download a file from storage.

        Args:
            path: Path to the file in storage.

        Yields:
            Chunks of file content.
        """
        pass

    @abstractmethod
    async def delete(self, path: str) -> StorageResult:
        """Delete a file from storage.

        Args:
            path: Path to the file in storage.

        Returns:
            StorageResult with success status.
        """
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file exists.

        Args:
            path: Path to check.

        Returns:
            True if file exists, False otherwise.
        """
        pass

    @abstractmethod
    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a signed URL for temporary access.

        Args:
            path: Path to the file.
            expires_in: URL expiration in seconds.

        Returns:
            Signed URL for temporary access.
        """
        pass


__all__ = ["StorageProvider", "StorageResult"]
