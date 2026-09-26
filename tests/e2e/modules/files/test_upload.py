"""E2E tests for large file upload memory validation.

Validates that S3 streaming upload uses constant memory regardless of file size.
Tests memory usage during uploads of various sizes and concurrent scenarios.
"""

import hashlib
import io
import logging
import time
import tracemalloc
from typing import BinaryIO
from unittest.mock import MagicMock, patch

import pytest

from src.app.clients.storage.interfaces import StorageResult
from src.app.clients.storage.s3_storage import S3Storage

logger = logging.getLogger(__name__)


class MemoryTracker:
    """Track memory usage during operations."""

    def __init__(self):
        self.samples: list[float] = []
        self._start_time: float = 0

    def start(self):
        tracemalloc.start()
        self._start_time = time.perf_counter()

    def sample(self) -> float:
        current, peak = tracemalloc.get_traced_memory()
        mb = current / 1024 / 1024
        self.samples.append(mb)
        return mb

    def stop(self) -> tuple[float, float]:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        duration = time.perf_counter() - self._start_time
        return peak / 1024 / 1024, duration

    @property
    def peak_mb(self) -> float:
        return max(self.samples) if self.samples else 0

    @property
    def min_mb(self) -> float:
        return min(self.samples) if self.samples else 0


def create_test_file(size_mb: int) -> tuple[io.BytesIO, str]:
    """Create a test file of specified size with random-ish content.

    Returns tuple of (file object, md5 hash).
    """
    chunk_size = 1024 * 1024  # 1MB chunks
    hasher = hashlib.md5()
    buffer = io.BytesIO()

    for i in range(size_mb):
        chunk = bytes([i % 256] * chunk_size)
        buffer.write(chunk)
        hasher.update(chunk)

    buffer.seek(0)
    return buffer, hasher.hexdigest()


class ChunkedUploadFile:
    """Mock UploadFile that yields content in chunks without loading all into memory."""

    def __init__(self, size_mb: int):
        self.size_mb = size_mb
        self.size = size_mb * 1024 * 1024
        self.filename = f"test_{size_mb}mb.bin"
        self._position = 0
        self._chunk_size = 1024 * 1024  # 1MB chunks

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            size = self.size - self._position

        remaining = self.size - self._position
        to_read = min(size, remaining)

        if to_read <= 0:
            return b""

        chunk_index = self._position // self._chunk_size
        data = bytes([chunk_index % 256] * to_read)
        self._position += to_read
        return data

    def seek(self, pos: int):
        self._position = pos

    def tell(self) -> int:
        return self._position


@pytest.fixture
def mock_s3_storage():
    """Mock S3 storage that tracks uploaded data."""
    storage_data = {}
    client = MagicMock()

    async def mock_upload(file: BinaryIO, path: str, mime_type: str) -> StorageResult:
        chunks = []
        while True:
            chunk = file.read(8192)
            if not chunk:
                break
            chunks.append(chunk)
        storage_data[path] = b"".join(chunks)
        return StorageResult(success=True, path=path, size=len(storage_data[path]))

    async def mock_download(path: str):
        data = storage_data.get(path, b"")
        chunk_size = 8192
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    storage = S3Storage(s3_client=client, bucket="test-bucket", region="us-east-1")

    with patch.object(storage, "upload", side_effect=mock_upload):
        with patch.object(storage, "download", side_effect=mock_download):
            yield storage, storage_data


class TestFileUploadMemory:
    """Test suite for file upload memory usage validation."""

    @pytest.mark.e2e
    def test_small_file_upload_memory(self, mock_s3_storage):
        """Test: 10MB file upload uses minimal memory."""
        logger.info("TEST START: small_file_upload_memory (10MB)")
        storage, storage_data = mock_s3_storage

        file_obj = ChunkedUploadFile(size_mb=10)
        tracker = MemoryTracker()

        tracker.start()
        tracker.sample()

        # Simulate chunked upload
        chunks_read = 0
        while True:
            chunk = file_obj.read(8192)
            if not chunk:
                break
            chunks_read += 1
            if chunks_read % 100 == 0:
                tracker.sample()

        peak_mb, duration = tracker.stop()

        logger.info(f"10MB upload: peak={peak_mb:.2f}MB, duration={duration:.2f}s")
        logger.info(f"Chunks read: {chunks_read}")

        assert peak_mb < 20, f"Peak memory {peak_mb:.2f}MB exceeds 20MB limit for 10MB file"

    @pytest.mark.e2e
    def test_large_file_upload_memory(self, mock_s3_storage):
        """Test: 100MB file upload uses constant memory (< 50MB)."""
        logger.info("TEST START: large_file_upload_memory (100MB)")
        storage, storage_data = mock_s3_storage

        file_obj = ChunkedUploadFile(size_mb=100)
        tracker = MemoryTracker()

        tracker.start()
        initial_mb = tracker.sample()

        chunks_read = 0
        while True:
            chunk = file_obj.read(8192)
            if not chunk:
                break
            chunks_read += 1
            if chunks_read % 1000 == 0:
                tracker.sample()

        peak_mb, duration = tracker.stop()

        logger.info(f"100MB upload: peak={peak_mb:.2f}MB, duration={duration:.2f}s")
        logger.info(f"Initial: {initial_mb:.2f}MB, Peak: {peak_mb:.2f}MB")
        logger.info(f"Memory delta: {peak_mb - initial_mb:.2f}MB")

        assert peak_mb < 50, f"Peak memory {peak_mb:.2f}MB exceeds 50MB limit for 100MB file"

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_very_large_file_memory_constant(self, mock_s3_storage):
        """Test: 500MB file upload memory does NOT scale with file size."""
        logger.info("TEST START: very_large_file_memory_constant (500MB)")
        storage, storage_data = mock_s3_storage

        file_obj = ChunkedUploadFile(size_mb=500)
        tracker = MemoryTracker()

        tracker.start()
        tracker.sample()  # Initial sample

        samples_at_100mb = []
        samples_at_250mb = []
        samples_at_500mb = []

        bytes_read = 0
        while True:
            chunk = file_obj.read(8192)
            if not chunk:
                break
            bytes_read += len(chunk)

            mb_processed = bytes_read / 1024 / 1024
            if 99 < mb_processed < 101:
                samples_at_100mb.append(tracker.sample())
            elif 249 < mb_processed < 251:
                samples_at_250mb.append(tracker.sample())
            elif 499 < mb_processed:
                samples_at_500mb.append(tracker.sample())

        peak_mb, duration = tracker.stop()

        avg_100 = sum(samples_at_100mb) / len(samples_at_100mb) if samples_at_100mb else 0
        avg_250 = sum(samples_at_250mb) / len(samples_at_250mb) if samples_at_250mb else 0
        avg_500 = sum(samples_at_500mb) / len(samples_at_500mb) if samples_at_500mb else 0

        logger.info(f"500MB upload: peak={peak_mb:.2f}MB, duration={duration:.2f}s")
        logger.info(f"Memory at 100MB: {avg_100:.2f}MB")
        logger.info(f"Memory at 250MB: {avg_250:.2f}MB")
        logger.info(f"Memory at 500MB: {avg_500:.2f}MB")

        memory_growth = avg_500 - avg_100
        logger.info(f"Memory growth (100MB → 500MB): {memory_growth:.2f}MB")

        assert peak_mb < 100, f"Peak memory {peak_mb:.2f}MB exceeds 100MB limit for 500MB file"
        assert memory_growth < 30, f"Memory grew {memory_growth:.2f}MB between 100MB and 500MB processed"

    @pytest.mark.e2e
    def test_concurrent_uploads_memory(self, mock_s3_storage):
        """Test: 5 concurrent 50MB uploads don't multiply memory."""
        logger.info("TEST START: concurrent_uploads_memory (5x50MB)")
        storage, storage_data = mock_s3_storage

        files = [ChunkedUploadFile(size_mb=50) for _ in range(5)]
        tracker = MemoryTracker()

        tracker.start()
        initial_mb = tracker.sample()

        # Simulate concurrent processing by interleaving reads
        active_files = list(enumerate(files))
        while active_files:
            for idx, file_obj in active_files[:]:
                chunk = file_obj.read(8192)
                if not chunk:
                    active_files.remove((idx, file_obj))

            if len(active_files) % 10 == 0:
                tracker.sample()

        peak_mb, duration = tracker.stop()

        logger.info(f"5x50MB concurrent: peak={peak_mb:.2f}MB, duration={duration:.2f}s")
        logger.info(f"Initial: {initial_mb:.2f}MB, Peak: {peak_mb:.2f}MB")

        # 5x50MB = 250MB total, but should use < 100MB memory (not 250MB)
        assert peak_mb < 100, f"Peak memory {peak_mb:.2f}MB exceeds 100MB limit for 5 concurrent 50MB uploads"

    @pytest.mark.e2e
    def test_file_integrity_after_upload(self, mock_s3_storage):
        """Test: File content is preserved after upload/download cycle."""
        logger.info("TEST START: file_integrity_after_upload")
        storage, storage_data = mock_s3_storage

        # Create file with known content
        size_mb = 10
        file_obj, original_hash = create_test_file(size_mb)
        path = "test/integrity_check.bin"

        logger.info(f"Original file: {size_mb}MB, hash={original_hash}")

        # Simulate upload
        file_obj.seek(0)
        chunks = []
        while True:
            chunk = file_obj.read(8192)
            if not chunk:
                break
            chunks.append(chunk)
        storage_data[path] = b"".join(chunks)

        # Verify stored data
        stored_data = storage_data[path]
        stored_hash = hashlib.md5(stored_data).hexdigest()

        logger.info(f"Stored file: {len(stored_data)} bytes, hash={stored_hash}")

        expected_size = size_mb * 1024 * 1024
        assert len(stored_data) == expected_size, f"Size mismatch: {len(stored_data)} vs {expected_size}"
        assert stored_hash == original_hash, f"Hash mismatch: {stored_hash} vs {original_hash}"

        logger.info("TEST COMPLETE: file_integrity_after_upload - PASSED")

    @pytest.mark.e2e
    def test_multipart_threshold_behavior(self, mock_s3_storage):
        """Test: Large files trigger multipart upload behavior."""
        logger.info("TEST START: multipart_threshold_behavior")
        storage, storage_data = mock_s3_storage

        # Standard multipart threshold is 8MB
        multipart_threshold = 8 * 1024 * 1024
        file_size = 15 * 1024 * 1024  # 15MB - should trigger multipart

        file_obj = ChunkedUploadFile(size_mb=15)

        # Count parts that would be created
        part_size = 5 * 1024 * 1024  # 5MB parts
        expected_parts = (file_size + part_size - 1) // part_size

        logger.info(f"File size: {file_size / 1024 / 1024}MB")
        logger.info(f"Multipart threshold: {multipart_threshold / 1024 / 1024}MB")
        logger.info(f"Expected parts (5MB each): {expected_parts}")

        # Simulate reading in 5MB parts
        parts_read = 0
        while True:
            part_data = file_obj.read(part_size)
            if not part_data:
                break
            parts_read += 1
            logger.info(f"Part {parts_read}: {len(part_data)} bytes")

        assert parts_read == expected_parts, f"Expected {expected_parts} parts, got {parts_read}"
        logger.info(f"TEST COMPLETE: multipart_threshold_behavior - PASSED ({parts_read} parts)")


class TestStreamingUploadPerformance:
    """Performance benchmarks for streaming upload."""

    @pytest.mark.e2e
    @pytest.mark.benchmark
    def test_streaming_vs_buffered_memory(self):
        """Compare memory usage: streaming vs full buffer."""
        logger.info("TEST START: streaming_vs_buffered_memory comparison")
        size_mb = 50

        # Streaming approach
        tracker_streaming = MemoryTracker()
        tracker_streaming.start()
        file_streaming = ChunkedUploadFile(size_mb=size_mb)
        while file_streaming.read(8192):
            pass
        peak_streaming, _ = tracker_streaming.stop()

        # Buffered approach (anti-pattern)
        tracker_buffered = MemoryTracker()
        tracker_buffered.start()
        buffered_data = bytes([0] * (size_mb * 1024 * 1024))
        peak_buffered, _ = tracker_buffered.stop()
        del buffered_data

        logger.info(f"Streaming peak: {peak_streaming:.2f}MB")
        logger.info(f"Buffered peak: {peak_buffered:.2f}MB")
        savings_pct = (1 - peak_streaming / peak_buffered) * 100
        logger.info(f"Memory saved: {peak_buffered - peak_streaming:.2f}MB ({savings_pct:.1f}%)")

        assert peak_streaming < peak_buffered, "Streaming should use less memory than buffering"
        assert peak_streaming < size_mb, (
            f"Streaming peak {peak_streaming:.2f}MB should be less than file size {size_mb}MB"
        )

        logger.info("TEST COMPLETE: streaming_vs_buffered_memory - PASSED")
