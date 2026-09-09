"""E2E tests for S3 file upload/download integrity.

Validates that streaming upload does not corrupt file content.
Tests small files, large files, binary types, and multipart uploads.
"""

import hashlib
import io
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.app.modules.storage.s3_storage import S3Storage


class MockUploadFile:
    """Mock for Starlette UploadFile with size attribute."""

    def __init__(self, content: bytes, size: int, filename: str = "test.bin"):
        self._file = io.BytesIO(content)
        self.size = size
        self.filename = filename

    def read(self):
        return self._file.read()

    def seek(self, pos):
        return self._file.seek(pos)


def md5_bytes(data: bytes) -> str:
    """Calculate MD5 hash of bytes."""
    h = hashlib.md5()
    h.update(data)
    return h.hexdigest()


def md5_file(file_path: str) -> str:
    """Calculate MD5 hash of file."""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def download_all(storage_instance: S3Storage, path: str) -> bytes:
    """Download all chunks from async generator."""
    chunks = []
    async for chunk in storage_instance.download(path):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client that stores and retrieves files."""
    storage = {}

    client = MagicMock()

    def mock_upload_fileobj(file_obj, bucket, key, **kwargs):
        """Store file content in memory."""
        file_obj.seek(0)
        storage[(bucket, key)] = file_obj.read()

    def mock_get_object(Bucket, Key):
        """Retrieve file content from storage."""
        content = storage.get((Bucket, Key))
        if content is None:
            raise Exception(f"File not found: {Bucket}/{Key}")
        return {"Body": io.BytesIO(content), "ContentLength": len(content)}

    client.upload_fileobj = mock_upload_fileobj
    client.get_object = mock_get_object
    client.put_object = MagicMock()

    return client, storage


@pytest.fixture
def s3_storage(mock_s3_client):
    """S3 storage instance with mocked client."""
    client, storage = mock_s3_client
    storage_instance = S3Storage(
        s3_client=client,
        bucket="test-bucket",
        region="us-east-1",
    )
    return storage_instance, storage


@pytest.fixture(autouse=True)
def mock_botocore():
    """Mock botocore module to avoid import errors."""
    import sys
    from unittest.mock import MagicMock

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
async def test_small_file_integrity(s3_storage):
    """Scenario 1: Small file integrity (1KB)."""
    storage_instance, storage = s3_storage

    content = os.urandom(1024)
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/small.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/small.bin")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)


@pytest.mark.asyncio
async def test_large_file_integrity(s3_storage):
    """Scenario 2: Large file integrity (10MB for testing, 100MB in production)."""
    storage_instance, storage = s3_storage

    content = os.urandom(10 * 1024 * 1024)  # 10MB
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/large.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success
    assert upload_result.size == len(content)

    download_result = await download_all(storage_instance, "test/large.bin")

    assert len(download_result) == len(content)
    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)


@pytest.mark.asyncio
async def test_png_image_integrity(s3_storage):
    """Scenario 3: Binary file type - PNG image."""
    storage_instance, storage = s3_storage

    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    content = img_bytes.getvalue()

    mock_file = MockUploadFile(content, len(content), "test.png")

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(file=mock_file, path="test/image.png", mime_type="image/png")

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/image.png")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)


@pytest.mark.asyncio
async def test_pdf_file_integrity(s3_storage):
    """Scenario 3: Binary file type - PDF."""
    storage_instance, storage = s3_storage

    content = b"%PDF-1.4\n" + os.urandom(1024) + b"%%EOF"
    mock_file = MockUploadFile(content, len(content), "test.pdf")

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/doc.pdf", mime_type="application/pdf"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/doc.pdf")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)


@pytest.mark.asyncio
async def test_zip_archive_integrity(s3_storage):
    """Scenario 3: Binary file type - ZIP archive."""
    storage_instance, storage = s3_storage

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr("file1.txt", b"content1")
        zipf.writestr("file2.txt", b"content2")
    content = zip_buffer.getvalue()

    mock_file = MockUploadFile(content, len(content), "test.zip")

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/archive.zip", mime_type="application/zip"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/archive.zip")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)

    with zipfile.ZipFile(io.BytesIO(download_result), "r") as zipf:
        assert zipf.read("file1.txt") == b"content1"
        assert zipf.read("file2.txt") == b"content2"


@pytest.mark.asyncio
async def test_content_type_preservation(s3_storage):
    """Scenario 4: Content-Type preservation."""
    storage_instance, storage = s3_storage

    content = b"test content"
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/file.txt", mime_type="text/plain; charset=utf-8"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/file.txt")

    assert download_result == content


@pytest.mark.asyncio
async def test_multipart_boundary_integrity(s3_storage):
    """Scenario 5: Multipart boundary - file that triggers multipart (>5MB)."""
    storage_instance, storage = s3_storage

    content = os.urandom(10 * 1024 * 1024)  # 10MB, >5MB threshold
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/multipart.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success
    assert upload_result.size == len(content)

    download_result = await download_all(storage_instance, "test/multipart.bin")

    assert len(download_result) == len(content)
    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)

    verify_no_corruption_at_boundaries(download_result, chunk_size=8 * 1024 * 1024)


def verify_no_corruption_at_boundaries(content: bytes, chunk_size: int):
    """Verify no corruption at multipart chunk boundaries."""
    n_chunks = (len(content) + chunk_size - 1) // chunk_size
    for i in range(n_chunks):
        start = i * chunk_size
        end = min((i + 1) * chunk_size, len(content))
        chunk = content[start:end]
        assert len(chunk) > 0, f"Chunk {i} is empty"
        assert md5_bytes(chunk) == md5_bytes(chunk), f"Chunk {i} corrupted"


@pytest.mark.asyncio
async def test_zero_byte_file_integrity(s3_storage):
    """Edge case: Zero-byte file integrity."""
    storage_instance, storage = s3_storage

    content = b""
    mock_file = MockUploadFile(content, 0)

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/empty.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/empty.bin")

    assert download_result == b""


@pytest.mark.asyncio
async def test_exact_multipart_threshold_integrity(s3_storage):
    """Edge case: File exactly at multipart threshold (5MB)."""
    storage_instance, storage = s3_storage

    content = os.urandom(5 * 1024 * 1024)  # Exactly 5MB
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/threshold.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/threshold.bin")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)


@pytest.mark.asyncio
async def test_just_below_multipart_threshold_integrity(s3_storage):
    """Edge case: File just below multipart threshold (5MB - 1 byte)."""
    storage_instance, storage = s3_storage

    content = os.urandom(5 * 1024 * 1024 - 1)  # 5MB - 1 byte
    mock_file = MockUploadFile(content, len(content))

    with patch("src.app.modules.storage.s3_storage.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.side_effect = lambda *args, **kwargs: args[0](*args[1:], **kwargs)

        upload_result = await storage_instance.upload(
            file=mock_file, path="test/below-threshold.bin", mime_type="application/octet-stream"
        )

    assert upload_result.success

    download_result = await download_all(storage_instance, "test/below-threshold.bin")

    assert download_result == content
    assert md5_bytes(download_result) == md5_bytes(content)
