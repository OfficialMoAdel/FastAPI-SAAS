"""Storage provider interfaces and abstractions.

This module defines the abstract interface for all storage providers,
ensuring consistent API across S3, Azure Blob, Google Cloud Storage,
and local file system implementations.

It is the seam's abstract half and deliberately imports nothing from the
project, so the outbound package and the storage feature can both name it
without depending on each other.

Usage:
    from src.app.clients.storage import StorageProvider, StorageResult

    class MyStorage(StorageProvider):
        async def upload(self, file, path, mime_type):
            ...
"""

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
    def download(self, path: str) -> AsyncGenerator[bytes, None]:
        """Download a file from storage.

        Args:
            path: Path to the file in storage.

        Yields:
            Chunks of file content.
        """
        ...

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

    async def close(self) -> None:
        """Release any transport resources this provider holds.

        NOT abstract: LocalStorage has no client to close, and a
        default no-op lets callers invoke it unconditionally instead of
        probing with ``hasattr`` for a capability the interface never
        declared. Providers that hold a client (S3, Azure) override it.

        Safe to call more than once.
        """
        return None

    @abstractmethod
    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a URL for the stored object.

        The returned URL has three semantics depending on the backend
        (P2-#10):

        - S3 / Azure: an EXPIRING presigned / SAS bearer URL valid for
          ``expires_in`` seconds. On failure S3 raises
          ``StorageBackendError`` — it never returns a bogus URL.
        - Local: a NON-expiring in-API-style path (e.g. ``/files/...``).
          It is NOT a bearer URL — there is no static mount serving it;
          security is enforced at the application layer.

        Callers that need authenticated file access for Local objects
        should use ``StorageService.get_presigned_url``, which maps the
        Local backend to the authenticated in-API route
        ``/api/v1/files/{file_id}``.

        Args:
            path: Path to the file.
            expires_in: URL expiration in seconds (remote backends only).

        Returns:
            Signed URL for temporary access (remote) or in-API path (Local).
        """
        pass


__all__ = ["StorageProvider", "StorageResult"]
