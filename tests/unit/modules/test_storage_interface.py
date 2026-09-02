"""Unit tests for storage provider interface.

Tests follow TDD approach - these tests define the expected behavior
for storage providers that feature modules can use.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


class MockClientError(Exception):
    """Mock ClientError for testing without boto3 installed."""

    def __init__(self, error_response, operation_name):
        self.error_response = error_response
        self.operation_name = operation_name
        super().__init__(str(error_response))


class TestStorageProviderInterface:
    """Test storage provider interface."""

    def test_storage_provider_is_abc(self):
        """Test that StorageProvider is properly defined."""
        from abc import ABC

        from src.app.modules.storage.interfaces import StorageProvider

        # Verify it's an ABC
        assert issubclass(StorageProvider, ABC)

        # Verify abstract methods exist
        assert hasattr(StorageProvider, "upload")
        assert hasattr(StorageProvider, "download")
        assert hasattr(StorageProvider, "delete")
        assert hasattr(StorageProvider, "exists")
        assert hasattr(StorageProvider, "get_url")

    def test_storage_provider_cannot_instantiate(self):
        """Test that StorageProvider cannot be instantiated directly."""
        from src.app.modules.storage.interfaces import StorageProvider

        with pytest.raises(TypeError):
            StorageProvider()

    def test_storage_result_dataclass(self):
        """Test StorageResult structure."""
        from src.app.modules.storage.interfaces import StorageResult

        result = StorageResult(success=True, path="/files/test.txt", size=1024)

        assert result.success is True
        assert result.path == "/files/test.txt"
        assert result.size == 1024
        assert result.error is None

    def test_storage_result_with_error(self):
        """Test StorageResult with error."""
        from src.app.modules.storage.interfaces import StorageResult

        result = StorageResult(success=False, path="", error="File not found")

        assert result.success is False
        assert result.error == "File not found"

    def test_storage_result_defaults(self):
        """Test StorageResult default values."""
        from src.app.modules.storage.interfaces import StorageResult

        result = StorageResult(success=True, path="/test/file.txt")

        assert result.success is True
        assert result.path == "/test/file.txt"
        assert result.size is None
        assert result.error is None


class TestS3Storage:
    """Test S3 storage implementation."""

    @pytest.fixture
    def mock_s3_client(self):
        """Fixture for mock S3 client."""
        client = MagicMock()
        client.put_object = MagicMock(return_value={"ETag": '"test"'})
        client.get_object = MagicMock()
        client.delete_object = MagicMock()
        client.head_object = MagicMock()
        client.generate_presigned_url = MagicMock(return_value="https://signed-url")
        return client

    def test_s3_storage_implements_interface(self, mock_s3_client):
        """Test that S3Storage implements StorageProvider."""
        from src.app.modules.storage.interfaces import StorageProvider
        from src.app.modules.storage.s3_storage import S3Storage

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        assert isinstance(storage, StorageProvider)

    def test_s3_storage_init_with_client(self, mock_s3_client):
        """Test S3Storage initialization with provided client."""
        from src.app.modules.storage.s3_storage import S3Storage

        storage = S3Storage(s3_client=mock_s3_client, bucket="my-bucket")

        assert storage.bucket == "my-bucket"
        assert storage.client is mock_s3_client

    def test_s3_storage_init_with_defaults(self, mock_s3_client):
        """Test S3Storage initialization with defaults."""
        from src.app.modules.storage.s3_storage import S3Storage

        # When s3_client is provided, boto3 should not be imported
        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket", region="us-east-1")

        assert storage.bucket == "test-bucket"
        assert storage.region == "us-east-1"

    @pytest.mark.asyncio
    async def test_s3_upload(self, mock_s3_client):
        """Test S3 file upload."""

        from src.app.modules.storage.s3_storage import S3Storage

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        file_content = b"test file content"
        file_obj = BytesIO(file_content)

        # Disable streaming uploads so the legacy path (which computes size from the
        # spooled temp file) is exercised. Production reads the delegated flag via
        # settings.performance.FEATURE_STREAMING_UPLOADS, so patch it there.
        with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
            mock_settings.performance.FEATURE_STREAMING_UPLOADS = False
            result = await storage.upload(file=file_obj, path="uploads/test.txt", mime_type="text/plain")

        assert result.success is True
        assert "uploads/test.txt" in result.path
        # Legacy path now correctly computes size (17 bytes for b"test file content").
        assert result.size == 17
        mock_s3_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_s3_upload_failure(self, mock_s3_client):
        """Test S3 file upload failure handling."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.put_object.side_effect = MockClientError(
            {"Error": {"Code": "500", "Message": "Internal Error"}}, "PutObject"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        file_obj = BytesIO(b"test content")

        result = await storage.upload(file=file_obj, path="uploads/test.txt", mime_type="text/plain")

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_s3_download(self, mock_s3_client):
        """Test S3 file download."""
        from src.app.modules.storage.s3_storage import S3Storage

        # Mock streaming body
        mock_body = MagicMock()
        mock_body.read.side_effect = [b"chunk1", b"chunk2", b""]
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        chunks = []
        async for chunk in storage.download("uploads/test.txt"):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0] == b"chunk1"
        assert chunks[1] == b"chunk2"

    @pytest.mark.asyncio
    async def test_s3_delete(self, mock_s3_client):
        """Test S3 file deletion."""
        from src.app.modules.storage.s3_storage import S3Storage

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        result = await storage.delete("uploads/test.txt")

        assert result.success is True
        assert result.path == "uploads/test.txt"
        mock_s3_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="uploads/test.txt")

    @pytest.mark.asyncio
    async def test_s3_delete_failure(self, mock_s3_client):
        """Test S3 deletion failure on a real error (404 is idempotent success)."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.delete_object.side_effect = MockClientError(
            {"Error": {"Code": "500", "Message": "Internal Error"}}, "DeleteObject"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        result = await storage.delete("uploads/test.txt")

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_s3_exists_true(self, mock_s3_client):
        """Test S3 file exists check - file exists."""
        from src.app.modules.storage.s3_storage import S3Storage

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        exists = await storage.exists("uploads/test.txt")

        assert exists is True
        mock_s3_client.head_object.assert_called_once_with(Bucket="test-bucket", Key="uploads/test.txt")

    @pytest.mark.asyncio
    async def test_s3_exists_false(self, mock_s3_client):
        """Test S3 file exists check - file does not exist."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.head_object.side_effect = MockClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        exists = await storage.exists("uploads/nonexistent.txt")

        assert exists is False

    @pytest.mark.asyncio
    async def test_s3_exists_403_raises(self, mock_s3_client):
        """403 (access denied) must NOT masquerade as missing file — it raises."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.head_object.side_effect = MockClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        with pytest.raises(Exception, match="Forbidden"):
            await storage.exists("uploads/secret.txt")

    @pytest.mark.asyncio
    async def test_s3_delete_missing_object_is_idempotent_success(self, mock_s3_client):
        """Deleting an absent object is a success (matches Azure semantics)."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.delete_object.side_effect = MockClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "DeleteObject"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        result = await storage.delete("uploads/gone.txt")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_s3_get_url(self, mock_s3_client):
        """Test S3 signed URL generation."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.generate_presigned_url.return_value = (
            "https://s3.amazonaws.com/test-bucket/uploads/test.txt?signature=xyz"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        url = await storage.get_url("uploads/test.txt", expires_in=7200)

        assert "https://" in url
        assert "test-bucket" in url or "test.txt" in url
        mock_s3_client.generate_presigned_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_s3_get_url_failure(self, mock_s3_client):
        """Test S3 signed URL generation failure."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_s3_client.generate_presigned_url.side_effect = MockClientError(
            {"Error": {"Code": "500", "Message": "Internal Error"}}, "GeneratePresignedUrl"
        )

        storage = S3Storage(s3_client=mock_s3_client, bucket="test-bucket")

        url = await storage.get_url("uploads/test.txt")

        assert url == ""


class TestStorageModuleExports:
    """Test storage module exports all required classes."""

    def test_storage_module_exports_interfaces(self):
        """Test that storage module exports interface classes."""
        from src.app.modules.storage import (
            StorageProvider,
            StorageResult,
        )

        assert StorageProvider is not None
        assert StorageResult is not None

    def test_storage_module_no_global_factory(self):
        """The stale global-settings factory is gone; provider selection is tenant-scoped."""
        import src.app.modules.storage as storage_pkg

        assert not hasattr(storage_pkg, "get_storage")
