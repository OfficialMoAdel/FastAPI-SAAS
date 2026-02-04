"""Unit tests for S3 streaming upload optimization.

Tests the streaming upload feature that uses upload_fileobj instead of
reading the entire file into memory.
"""

import asyncio
import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from src.app.modules.storage.s3_storage import S3Storage
from src.app.modules.storage.interfaces import StorageResult


class MockUploadFile:
    """Mock for Starlette UploadFile with size attribute."""

    def __init__(self, content: bytes, size: int):
        self._file = BytesIO(content)
        self.size = size

    def read(self):
        return self._file.read()

    def seek(self, pos):
        return self._file.seek(pos)


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    client = MagicMock()
    client.upload_fileobj = MagicMock()
    client.put_object = MagicMock()
    return client


@pytest.fixture
def s3_storage(mock_s3_client):
    """S3 storage instance with mocked client."""
    return S3Storage(
        s3_client=mock_s3_client,
        bucket="test-bucket",
        region="us-east-1",
    )


@pytest.fixture(autouse=True)
def mock_botocore():
    """Mock botocore module to avoid import errors."""
    mock_botocore = MagicMock()
    mock_config = MagicMock()
    mock_config.TransferConfig = MagicMock
    mock_botocore.config = mock_config
    sys.modules["botocore"] = mock_botocore
    sys.modules["botocore.config"] = mock_config
    yield
    sys.modules.pop("botocore", None)
    sys.modules.pop("botocore.config", None)


@pytest.mark.asyncio
async def test_streaming_upload_with_feature_flag(s3_storage, mock_s3_client):
    """Test streaming upload when FEATURE_STREAMING_UPLOADS is True."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"test file content" * 100
        mock_file = MockUploadFile(content, len(content))

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            result = await s3_storage.upload(
                file=mock_file,
                path="test/path.txt",
                mime_type="text/plain",
            )

            assert mock_s3_client.upload_fileobj.called
            assert not mock_s3_client.put_object.called

            call_args = mock_s3_client.upload_fileobj.call_args
            assert call_args[0][0] == mock_file
            assert call_args[0][1] == "test-bucket"
            assert call_args[0][2] == "test/path.txt"

            call_kwargs = call_args[1]
            assert "Config" in call_kwargs
            assert call_kwargs["ExtraArgs"]["ContentType"] == "text/plain"

            assert result.success
            assert result.path == "test/path.txt"
            assert result.size == len(content)


@pytest.mark.asyncio
async def test_streaming_upload_resets_file_position(s3_storage, mock_s3_client):
    """Test that streaming upload resets file position to 0 before upload."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"test content"
        mock_file = MockUploadFile(content, len(content))
        mock_file._file.seek(10)

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            await s3_storage.upload(
                file=mock_file,
                path="test.txt",
                mime_type="text/plain",
            )

            assert mock_file._file.tell() == 0


@pytest.mark.asyncio
async def test_streaming_upload_without_size_attribute(s3_storage, mock_s3_client):
    """Test streaming upload when file has no size attribute."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"test content"
        mock_file = BytesIO(content)

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            result = await s3_storage.upload(
                file=mock_file,
                path="test.txt",
                mime_type="text/plain",
            )

            assert result.success
            assert result.size == 0


@pytest.mark.asyncio
async def test_legacy_upload_when_feature_flag_false(s3_storage, mock_s3_client):
    """Test legacy upload (read into memory) when FEATURE_STREAMING_UPLOADS is False."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = False

        content = b"test content" * 100
        mock_file = BytesIO(content)

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            result = await s3_storage.upload(
                file=mock_file,
                path="test.txt",
                mime_type="text/plain",
            )

            assert mock_s3_client.put_object.called
            assert not mock_s3_client.upload_fileobj.called

            call_args = mock_s3_client.put_object.call_args
            call_kwargs = call_args[1]
            assert call_kwargs["Bucket"] == "test-bucket"
            assert call_kwargs["Key"] == "test.txt"
            assert call_kwargs["Body"] == content

            assert result.success
            assert result.size == len(content)


@pytest.mark.asyncio
async def test_streaming_upload_error_handling(s3_storage, mock_s3_client):
    """Test error handling in streaming upload."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"test content"
        mock_file = BytesIO(content)

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = Exception("S3 upload failed")

            result = await s3_storage.upload(
                file=mock_file,
                path="test.txt",
                mime_type="text/plain",
            )

            assert not result.success
            assert result.path == "test.txt"
            assert "S3 upload failed" in result.error


@pytest.mark.asyncio
async def test_legacy_upload_error_handling(s3_storage, mock_s3_client):
    """Test error handling in legacy upload."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = False

        content = b"test content"
        mock_file = BytesIO(content)

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = Exception("S3 put_object failed")

            result = await s3_storage.upload(
                file=mock_file,
                path="test.txt",
                mime_type="text/plain",
            )

            assert not result.success
            assert result.path == "test.txt"
            assert "S3 put_object failed" in result.error


@pytest.mark.asyncio
async def test_large_file_uses_multipart(s3_storage, mock_s3_client):
    """Test that large files trigger multipart upload."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"x" * (10 * 1024 * 1024)
        mock_file = MockUploadFile(content, len(content))

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            await s3_storage.upload(
                file=mock_file,
                path="largefile.bin",
                mime_type="application/octet-stream",
            )

            call_args = mock_s3_client.upload_fileobj.call_args
            call_kwargs = call_args[1]
            assert "Config" in call_kwargs


@pytest.mark.asyncio
async def test_small_file_uses_multipart_config(s3_storage, mock_s3_client):
    """Test that small files still get TransferConfig (multipart is automatic)."""
    with patch("src.app.modules.storage.s3_storage.settings") as mock_settings:
        mock_settings.performance.FEATURE_STREAMING_UPLOADS = True

        content = b"x" * 1000
        mock_file = MockUploadFile(content, len(content))

        with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

            await s3_storage.upload(
                file=mock_file,
                path="smallfile.txt",
                mime_type="text/plain",
            )

            call_args = mock_s3_client.upload_fileobj.call_args
            call_kwargs = call_args[1]
            assert "Config" in call_kwargs
