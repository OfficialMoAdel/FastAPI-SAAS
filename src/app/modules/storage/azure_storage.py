"""Azure Blob Storage provider implementation.

This module implements the StorageProvider interface for Azure Blob Storage,
providing async upload, download, delete, and signed URL generation.

Usage:
    from src.app.modules.storage.azure_storage import AzureBlobStorage

    storage = AzureBlobStorage(connection_string="...", container_name="files")
    result = await storage.upload(file, "path/to/file.pdf", "application/pdf")
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from src.app.modules.storage.interfaces import StorageProvider, StorageResult

logger = logging.getLogger(__name__)


class AzureBlobStorage(StorageProvider):
    """Azure Blob Storage implementation of StorageProvider.

    Attributes:
        connection_string: Azure Storage connection string.
        container_name: Name of the blob container.
    """

    def __init__(self, connection_string: str, container_name: str) -> None:
        """Initialize Azure Blob Storage provider.

        Args:
            connection_string: Azure Storage connection string.
            container_name: Name of the blob container to use.
        """
        self._connection_string = connection_string
        self._container_name = container_name
        self._service_client: BlobServiceClient | None = None

        # Cache parsed connection string values to avoid repeated parsing
        self._account_name = self._parse_account_name(connection_string)
        self._account_key = self._parse_account_key(connection_string)

    async def _get_container_client(self) -> ContainerClient:
        """Get or create container client.

        Returns:
            ContainerClient for the configured container.
        """
        if self._service_client is None:
            self._service_client = BlobServiceClient.from_connection_string(self._connection_string)
        return self._service_client.get_container_client(self._container_name)

    async def upload(
        self,
        file: BinaryIO,
        path: str,
        mime_type: str,
    ) -> StorageResult:
        """Upload a file to Azure Blob Storage.

        Args:
            file: File-like object to upload.
            path: Target path (blob name) in storage.
            mime_type: MIME type of the file.

        Returns:
            StorageResult with success status and details.
        """
        try:
            container = await self._get_container_client()
            blob_client = container.get_blob_client(path)

            # Read file content
            content = file.read()
            size = len(content)

            # Upload with content type
            await blob_client.upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type=mime_type),
            )

            logger.info(f"Uploaded blob: {path}, size: {size} bytes")
            return StorageResult(success=True, path=path, size=size)

        except Exception as e:
            logger.error(f"Failed to upload blob {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

    async def download(self, path: str) -> AsyncGenerator[bytes, None]:
        """Download a file from Azure Blob Storage.

        Args:
            path: Path (blob name) to the file in storage.

        Yields:
            Chunks of file content.
        """
        container = await self._get_container_client()
        blob_client = container.get_blob_client(path)

        stream = await blob_client.download_blob()
        async for chunk in stream.chunks():
            yield chunk

    async def delete(self, path: str) -> StorageResult:
        """Delete a file from Azure Blob Storage.

        Args:
            path: Path (blob name) to the file in storage.

        Returns:
            StorageResult with success status.
        """
        try:
            container = await self._get_container_client()
            blob_client = container.get_blob_client(path)
            await blob_client.delete_blob()

            logger.info(f"Deleted blob: {path}")
            return StorageResult(success=True, path=path)

        except ResourceNotFoundError:
            logger.warning(f"Blob not found for deletion: {path}")
            return StorageResult(success=True, path=path)  # Idempotent delete

        except Exception as e:
            logger.error(f"Failed to delete blob {path}: {e}")
            return StorageResult(success=False, path=path, error=str(e))

    async def exists(self, path: str) -> bool:
        """Check if a blob exists.

        Args:
            path: Path (blob name) to check.

        Returns:
            True if blob exists, False otherwise.
        """
        try:
            container = await self._get_container_client()
            blob_client = container.get_blob_client(path)
            await blob_client.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False

    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a signed URL (SAS token) for temporary access.

        Args:
            path: Path (blob name) to the file.
            expires_in: URL expiration in seconds (default: 1 hour).

        Returns:
            Signed URL with SAS token for temporary access.
        """
        container = await self._get_container_client()
        blob_client = container.get_blob_client(path)

        # Use cached account name and key (parsed once in __init__)
        # Generate SAS token
        sas_token = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container_name,
            blob_name=path,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

        return f"{blob_client.url}?{sas_token}"

    @staticmethod
    def _parse_account_name(connection_string: str) -> str:
        """Parse account name from connection string.

        Args:
            connection_string: Azure Storage connection string.

        Returns:
            Account name extracted from connection string.

        Raises:
            ValueError: If AccountName not found in connection string.
        """
        for part in connection_string.split(";"):
            if part.startswith("AccountName="):
                return part.split("=", 1)[1]
        raise ValueError("AccountName not found in connection string")

    @staticmethod
    def _parse_account_key(connection_string: str) -> str:
        """Parse account key from connection string.

        Args:
            connection_string: Azure Storage connection string.

        Returns:
            Account key extracted from connection string.

        Raises:
            ValueError: If AccountKey not found in connection string.
        """
        for part in connection_string.split(";"):
            if part.startswith("AccountKey="):
                return part.split("=", 1)[1]
        raise ValueError("AccountKey not found in connection string")

    async def validate_connection(self) -> bool:
        """Validate storage connection by attempting to access the container.

        Returns:
            True if connection is valid, False otherwise.
        """
        try:
            container = await self._get_container_client()
            await container.get_container_properties()
            return True
        except Exception as e:
            logger.error(f"Azure connection validation failed: {e}")
            return False

    async def close(self) -> None:
        """Close the storage client connections."""
        if self._service_client:
            await self._service_client.close()
            self._service_client = None


__all__ = ["AzureBlobStorage"]
