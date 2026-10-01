from __future__ import annotations

"""S3 storage provider implementation.

This module provides AWS S3 storage implementation conforming to
the StorageProvider interface.

Usage:
    from src.app.clients.storage.s3_storage import S3Storage

    storage = S3Storage(bucket="my-bucket", region="us-east-1")
    result = await storage.upload(file, "uploads/file.txt", "text/plain")

Note:
    boto3 is a DECLARED runtime dependency (dxub.9, DECIDED: DECLARE) and is
    imported lazily here, at client construction, so importing this module
    costs nothing until an S3 provider is actually built.
"""

import asyncio
import posixpath
from collections.abc import AsyncGenerator
from typing import Any, BinaryIO

from src.app.clients.storage.interfaces import StorageProvider, StorageResult
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# boto3 is imported inside ``__init__`` below, not here: the import is lazy so
# that merely importing this module does not build a client (or pay for the
# SDK) until a provider is actually constructed.


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

        # C1 FIX: Extract credentials to pass to boto3.client
        aws_access_key_id = kwargs.pop("aws_access_key_id", None)
        aws_secret_access_key = kwargs.pop("aws_secret_access_key", None)

        if s3_client:
            self.client = s3_client
        else:
            # Lazy import boto3 - only when actually creating a real client
            import boto3
            from botocore.config import Config

            client_kwargs = {
                "region_name": region,
                "config": Config(signature_version="s3v4"),
            }
            if aws_access_key_id:
                client_kwargs["aws_access_key_id"] = aws_access_key_id
            if aws_secret_access_key:
                client_kwargs["aws_secret_access_key"] = aws_secret_access_key

            self.client = boto3.client("s3", **client_kwargs)

        self.kwargs = kwargs

    @staticmethod
    def _validate_s3_key(path: str) -> str:
        """Validate and normalize S3 key, rejecting directory traversal.

        R4-07: S3 keys are not filesystem paths but must still reject ``..``
        traversal and absolute keys. Mirrors the local_storage guard.
        """
        if not path or path.startswith("/") or "//" in path:
            raise ValueError("Invalid S3 key: must be relative and not empty")
        # Reject any ``..`` segment BEFORE normalizing — otherwise ``a/../b``
        # silently resolves to ``b`` with a different key than requested.
        if any(seg == ".." for seg in path.split("/")):
            raise ValueError("Invalid S3 key: directory traversal detected")
        normalized = posixpath.normpath(path)
        if normalized.startswith("..") or "/../" in f"/{normalized}/" or normalized == "..":
            raise ValueError("Invalid S3 key: directory traversal detected")
        # normpath strips trailing slash and collapses .// — reject originals that relied on it
        if ".." in normalized.split("/"):
            raise ValueError("Invalid S3 key: directory traversal detected")
        return normalized

    async def close(self) -> None:
        """Close the S3 client."""
        if hasattr(self.client, "close"):
            await asyncio.to_thread(self.client.close)

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
        path = self._validate_s3_key(path)
        try:
            # P-M8: streaming upload_fileobj is the ONLY path. The legacy
            # fallback (copying the file into a SpooledTemporaryFile chunk by
            # chunk via to_thread, then put_object) did a synchronous
            # temp.write(chunk) on the event loop for files above the spool
            # threshold and has been deleted. Streaming has been
            # unconditional since P0-3; every deployment exercises this
            # path, and multipart handles both small and large files.
            # Reset file position to 0 (required for Starlette UploadFile wrapping SpooledTemporaryFile)
            if hasattr(file, "seek"):
                await asyncio.to_thread(file.seek, 0)

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

            # Get file size: prefer an explicit .size attribute (e.g. Starlette UploadFile);
            # otherwise derive it from the file object without disturbing its read position.
            size = getattr(file, "size", None)
            if size is None and hasattr(file, "seek"):
                current_pos = await asyncio.to_thread(file.tell)
                await asyncio.to_thread(file.seek, 0, 2)
                size = await asyncio.to_thread(file.tell)
                await asyncio.to_thread(file.seek, current_pos)

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

    async def download(self, path: str) -> AsyncGenerator[bytes]:
        """Download a file from S3.

        Args:
            path: Path (key) to the file in S3.

        Yields:
            Chunks of file content (8KB each).
        """
        path = self._validate_s3_key(path)
        try:
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
        finally:
            if "body" in locals() and hasattr(body, "close"):
                # Clean up S3 response body
                await asyncio.to_thread(body.close)

    async def delete(self, path: str) -> StorageResult:
        """Delete a file from S3.

        Idempotent: deleting an absent object is a success, matching the
        Azure adapter's semantics. Other failures return success=False with
        the error detail.

        Args:
            path: Path (key) to the file in S3.

        Returns:
            StorageResult with success status.
        """
        path = self._validate_s3_key(path)
        try:
            # Wrap blocking delete_object in asyncio.to_thread to avoid blocking event loop
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=path,
            )
            return StorageResult(success=True, path=path)
        except Exception as e:
            error_code = self._get_error_code(e)
            if error_code in ("404", "NoSuchKey"):
                logger.info("S3 delete of missing object treated as success", path=path)
                return StorageResult(success=True, path=path)
            logger.error("Failed to delete S3 object", path=path, error=e)
            return StorageResult(
                success=False,
                path=path,
                error=str(e),
            )

    async def exists(self, path: str) -> bool:
        """Check if file exists in S3.

        Only a definitive 404/NoSuchKey answer maps to False. Any other error
        (403 access denied, 500, network failure) is re-raised so callers
        never mistake a misconfiguration for a missing file.

        Args:
            path: Path (key) to check.

        Returns:
            True if file exists, False if definitively absent.

        Raises:
            Exception: On any S3 error other than not-found.
        """
        path = self._validate_s3_key(path)
        try:
            # Wrap blocking head_object in asyncio.to_thread to avoid blocking event loop
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=path,
            )
            return True
        except Exception as e:
            error_code = self._get_error_code(e)
            if error_code in ("404", "NoSuchKey"):
                return False

            from src.app.core.logger import get_logger

            get_logger(__name__).error("S3 exists check failed", error=e, path=path)
            raise

    async def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get signed URL for S3 object.

        Args:
            path: Path (key) to the file.
            expires_in: URL expiration in seconds (default: 1 hour).

        Returns:
            Signed URL for temporary access.

        Raises:
            StorageBackendError: If presigning fails (fail-closed — never
                emit a bogus empty URL that callers treat as success).
        """
        path = self._validate_s3_key(path)
        try:
            # Wrap blocking generate_presigned_url in asyncio.to_thread to avoid blocking event loop
            url = await asyncio.to_thread(
                self.client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": path,
                    # P2-5: force download — bearer presigned URLs must not
                    # render tenant-controlled content (e.g. SVG) inline.
                    "ResponseContentDisposition": "attachment",
                },
                ExpiresIn=expires_in,
            )
            return str(url)

        except Exception as e:
            from src.app.core.logger import get_logger
            from src.app.modules.storage.errors import StorageBackendError

            get_logger(__name__).error("S3 get_url failed", error=e)
            raise StorageBackendError(f"Failed to generate presigned URL: {e}") from e

    def _get_error_code(self, exc: Exception) -> str | None:
        """Extract AWS error code from botocore ClientError or test mock errors."""
        if hasattr(exc, "response"):
            response = getattr(exc, "response")
            if isinstance(response, dict):
                error = response.get("Error")
                if isinstance(error, dict):
                    return str(error.get("Code"))
        if hasattr(exc, "error_response"):
            response = getattr(exc, "error_response")
            if isinstance(response, dict):
                error = response.get("Error")
                if isinstance(error, dict):
                    return str(error.get("Code"))
        return None


__all__ = ["S3Storage"]
