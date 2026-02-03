"""Local filesystem storage provider implementation.

This module implements the StorageProvider interface for local filesystem,
providing async file operations using aiofiles.

Usage:
    from src.app.modules.storage.local_storage import LocalStorage

    storage = LocalStorage(base_path="/var/app/uploads")
    result = await storage.upload(file, "path/to/file.pdf", "application/pdf")
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import aiofiles
import aiofiles.os

from src.app.modules.storage.interfaces import StorageProvider, StorageResult

logger = logging.getLogger(__name__)

# Chunk size for reading/writing files (64KB)
CHUNK_SIZE = 64 * 1024


class LocalStorage(StorageProvider):
    """Local filesystem implementation of StorageProvider.

    Stores files in a configurable base directory with support for
    nested paths and async I/O operations.

    Attributes:
        base_path: Root directory for file storage.
        base_url: Base URL for generating file URLs.
    """

    def __init__(self, base_path: str, base_url: str = "/files") -> None:
        """Initialize local storage provider.

        Args:
            base_path: Root directory for file storage.
            base_url: Base URL for file access (default: /files).
        """
        self._base_path = Path(base_path)
        self._base_url = base_url.rstrip("/")

    def _get_full_path(self, path: str) -> Path:
        """Get full filesystem path for a relative path.

        Args:
            path: Relative path within storage.

        Returns:
            Full Path object.

        Raises:
            ValueError: If path attempts directory traversal.
        """
        # Prevent directory traversal attacks
        full_path = (self._base_path / path).resolve()
        if not str(full_path).startswith(str(self._base_path.resolve())):
            raise ValueError("Invalid path: directory traversal detected")
        return full_path

    async def upload(
        self,
        file: BinaryIO,
        path: str,
        mime_type: str,
    ) -> StorageResult:
        """Upload a file to local storage.

        Args:
            file: File-like object to upload.
            path: Target path in storage.
            mime_type: MIME type of the file (stored in metadata file).

        Returns:
            StorageResult with success status and details.
        """
        try:
            full_path = self._get_full_path(path)

            # Create parent directories
            await aiofiles.os.makedirs(full_path.parent, exist_ok=True)

            # Read content and get size
            # Use asyncio.to_thread to prevent blocking the event loop during file read
            import asyncio

            content = await asyncio.to_thread(file.read)
            size = len(content)

            # Write file
            async with aiofiles.open(full_path, "wb") as f:
                await f.write(content)

            # Store metadata (mime type) in a sidecar file
            meta_path = full_path.with_suffix(full_path.suffix + ".meta")
            async with aiofiles.open(meta_path, "w") as f:
                await f.write(mime_type)

            logger.info(f"Uploaded file: {path}, size: {size} bytes")
            return StorageResult(success=True, path=path, size=size)

        except ValueError as e:
            logger.error(f"Path validation failed for {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

        except Exception as e:
            logger.error(f"Failed to upload file {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

    async def download(self, path: str) -> AsyncGenerator[bytes, None]:
        """Download a file from local storage.

        Args:
            path: Path to the file in storage.

        Yields:
            Chunks of file content.
        """
        full_path = self._get_full_path(path)

        async with aiofiles.open(full_path, "rb") as f:
            while True:
                chunk = await f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    async def delete(self, path: str) -> StorageResult:
        """Delete a file from local storage.

        Args:
            path: Path to the file in storage.

        Returns:
            StorageResult with success status.
        """
        try:
            full_path = self._get_full_path(path)

            # Delete main file
            if await aiofiles.os.path.exists(full_path):
                await aiofiles.os.remove(full_path)

            # Delete metadata file if exists
            meta_path = full_path.with_suffix(full_path.suffix + ".meta")
            if await aiofiles.os.path.exists(meta_path):
                await aiofiles.os.remove(meta_path)

            logger.info(f"Deleted file: {path}")
            return StorageResult(success=True, path=path)

        except ValueError as e:
            logger.error(f"Path validation failed for {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

        except Exception as e:
            logger.error(f"Failed to delete file {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

    async def exists(self, path: str) -> bool:
        """Check if a file exists.

        Args:
            path: Path to check.

        Returns:
            True if file exists, False otherwise.
        """
        try:
            full_path = self._get_full_path(path)
            return await aiofiles.os.path.exists(full_path)
        except ValueError:
            return False

    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a URL for file access.

        Note: Local storage doesn't support signed URLs with expiration.
        Returns a direct URL path. Security should be handled at the
        application layer.

        Args:
            path: Path to the file.
            expires_in: Ignored for local storage.

        Returns:
            URL path for file access.
        """
        # URL encode the path for safe URL generation
        encoded_path = quote(path, safe="/")
        return f"{self._base_url}/{encoded_path}"

    async def validate_connection(self) -> bool:
        """Validate storage by checking base directory exists and is writable.

        Returns:
            True if storage is valid, False otherwise.
        """
        try:
            # Ensure base path exists
            await aiofiles.os.makedirs(self._base_path, exist_ok=True)

            # Test write access with a temp file
            test_file = self._base_path / ".write_test"
            async with aiofiles.open(test_file, "w") as f:
                await f.write("test")
            await aiofiles.os.remove(test_file)

            return True
        except Exception as e:
            logger.error(f"Local storage validation failed: {e}")
            return False

    async def get_mime_type(self, path: str) -> str | None:
        """Get stored MIME type for a file.

        Args:
            path: Path to the file.

        Returns:
            MIME type if metadata exists, None otherwise.
        """
        try:
            full_path = self._get_full_path(path)
            meta_path = full_path.with_suffix(full_path.suffix + ".meta")

            if await aiofiles.os.path.exists(meta_path):
                async with aiofiles.open(meta_path) as f:
                    return await f.read()
            return None
        except Exception:
            return None


__all__ = ["LocalStorage"]
