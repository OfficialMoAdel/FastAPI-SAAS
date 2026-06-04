from __future__ import annotations

"""S3 storage provider implementation.

This module provides AWS S3 storage implementation conforming to
the StorageProvider interface.

Usage:
    from src.app.modules.storage import S3Storage

    storage = S3Storage(bucket="my-bucket", region="us-east-1")
    result = await storage.upload(file, "uploads/file.txt", "text/plain")

Note:
    Requires boto3 package: pip install boto3
"""


import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, BinaryIO

from src.app.core.config import settings
from src.app.modules.storage.interfaces import StorageProvider, StorageResult

# Lazy import for boto3 - only needed at runtime, not for testing with mocks
if TYPE_CHECKING:
    pass


class S3Storage(StorageProvider):
    """AWS S3 storage provider implementation.

    Provides file storage operations using AWS S3.

    Attributes:
        bucket: S3 bucket name.
        region: AWS region.
        client: boto3 S3 client instance.
    """

    def __init__(
        self,
        s3_client: Any = None,
        bucket: str = "default-bucket",
        region: str = "us-east-1",
        **kwargs: Any,
    ):
        self.bucket = bucket
        self.region = region

        if s3_client:
            self.client = s3_client
        else:
            # Lazy import boto3 - only when actually creating a real client
            import boto3
            from botocore.config import Config

            self.client = boto3.client(
                "s3",
                region_name=region,
                config=Config(signature_version="s3v4"),
            )

        self.kwargs = kwargs

    async def upload(
        self,
        file: BinaryIO,
        path: str,
        mime_type: str,
    ) -> StorageResult:
        """Upload a file to S3.

        Args:
            file: File-like object to upload.
            path: Target path (key) in S3.
            mime_type: MIME type of the file.

        Returns:
            StorageResult with success status and file details.
        """
        try:
            if settings.FEATURE_STREAMING_UPLOADS:
                # Streaming upload with multipart support
                # Reset file position to 0 (required for Starlette UploadFile wrapping SpooledTemporaryFile)
                if hasattr(file, "seek"):
                    file.seek(0)

                from botocore.config import TransferConfig

                transfer_config = TransferConfig(
                    multipart_threshold=5 * 1024 * 1024,  # 5MB threshold for multipart upload
                    multipart_chunksize=8 * 1024 * 1024,  # 8MB chunks
                    max_concurrency=10,
                )

                # Use upload_fileobj for streaming (no full file read into memory)
                await asyncio.to_thread(
                    self.client.upload_fileobj,
                    file,
                    self.bucket,
                    path,
                    ExtraArgs={"ContentType": mime_type},
                    Config=transfer_config,
                )

                # Get file size from file object if available
                size = file.size if hasattr(file, "size") else 0
            else:
                # Legacy behavior: read entire file into memory
                content = file.read()

                # Upload to S3 - wrapped in asyncio.to_thread to avoid blocking event loop
                await asyncio.to_thread(
                    self.client.put_object,
                    Bucket=self.bucket,
                    Key=path,
                    Body=content,
                    ContentType=mime_type,
                    **self.kwargs,
                )

                size = len(content)

            return StorageResult(
                success=True,
                path=path,
                size=size,
            )
        except Exception as e:
            return StorageResult(
                success=False,
                path=path,
                error=str(e),
            )

    async def download(self, path: str) -> AsyncGenerator[bytes, None]:
        """Download a file from S3.

        Args:
            path: Path (key) to the file in S3.

        Yields:
            Chunks of file content (8KB each).
        """
        # Wrap blocking get_object in asyncio.to_thread to avoid blocking event loop
        response = await asyncio.to_thread(
            self.client.get_object,
            Bucket=self.bucket,
            Key=path,
        )

        body = response["Body"]

        while True:
            chunk = await asyncio.to_thread(body.read, 5 * 1024 * 1024)  # 5MB chunks
            if not chunk:
                break
            yield chunk

    async def delete(self, path: str) -> StorageResult:
        """Delete a file from S3.

        Args:
            path: Path (key) to the file in S3.

        Returns:
            StorageResult with success status.
        """
        try:
            # Wrap blocking delete_object in asyncio.to_thread to avoid blocking event loop
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=path,
            )
            return StorageResult(success=True, path=path)
        except Exception as e:
            return StorageResult(
                success=False,
                path=path,
                error=str(e),
            )

    async def exists(self, path: str) -> bool:
        """Check if file exists in S3.

        Args:
            path: Path (key) to check.

        Returns:
            True if file exists, False otherwise.
        """
        try:
            # Wrap blocking head_object in asyncio.to_thread to avoid blocking event loop
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=path,
            )
            return True
        except Exception:
            return False

    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get signed URL for S3 object.

        Args:
            path: Path (key) to the file.
            expires_in: URL expiration in seconds (default: 1 hour).

        Returns:
            Signed URL for temporary access, or empty string on failure.
        """
        try:
            # Wrap blocking generate_presigned_url in asyncio.to_thread to avoid blocking event loop
            url = await asyncio.to_thread(
                self.client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": path,
                },
                ExpiresIn=expires_in,
            )
            return str(url)

        except Exception:
            return ""


__all__ = ["S3Storage"]
