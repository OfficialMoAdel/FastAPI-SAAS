"""Unit tests for S3 streaming upload.

P-M8: streaming upload_fileobj is the only upload path; the legacy
spooled-temporary-file fallback was deleted. B13 removed the dead
FEATURE_STREAMING_UPLOADS flag entirely, so these tests exercise the
unconditional streaming path directly (no settings patching needed).
"""

import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from src.app.modules.storage.s3_storage import S3Storage


class MockUploadFile:
    """Mock for Starlette UploadFile with size attribute."""

    def __init__(self, content: bytes, size: int):
        self._file = BytesIO(content)
        self.size = size

    def seek(self, pos: int) -> None:  # noqa: D102
        self._file.seek(pos)

    def tell(self) -> int:  # noqa: D102
        return self._file.tell()


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
    mock_config = MagicMock()
    mock_config.return_value = MagicMock()
    sys.modules["botocore.config"] = mock_config
    yield
    sys.modules.pop("botocore.config", None)


@pytest.mark.asyncio
async def test_streaming_upload_with_feature_flag(s3_storage, mock_s3_client):
    """Test streaming upload path (the only path since P-M8)."""
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
        assert result.size == len(content)


@pytest.mark.asyncio
async def test_upload_always_streams_no_fallback(s3_storage, mock_s3_client):
    """P-M8: streaming is the ONLY upload path — the legacy spooled-temp
    fallback was deleted (B13: the FEATURE_STREAMING_UPLOADS flag itself is
    gone too; this test pins the unconditional-streaming behavior)."""
    content = b"test content" * 100
    mock_file = BytesIO(content)

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        result = await s3_storage.upload(
            file=mock_file,
            path="test.txt",
            mime_type="text/plain",
        )

    assert mock_s3_client.upload_fileobj.called
    assert not mock_s3_client.put_object.called

    assert result.success
    assert result.size == len(content)


@pytest.mark.asyncio
async def test_streaming_upload_error_handling(s3_storage, mock_s3_client):
    """Test error handling in streaming upload."""
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
async def test_large_file_uses_multipart(s3_storage, mock_s3_client):
    """Test that large files trigger multipart upload."""
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
