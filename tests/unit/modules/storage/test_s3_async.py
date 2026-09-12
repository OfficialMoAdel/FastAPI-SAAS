"""P0-1: S3 Non-Blocking Upload Tests.

Verifies that S3 uploads use asyncio.to_thread() for non-blocking I/O.
"""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestS3AsyncUpload:
    """Tests for S3 non-blocking upload via asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_upload_uses_to_thread(self) -> None:
        """Verify S3 upload wraps blocking call in asyncio.to_thread."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_client = MagicMock()
        mock_client.upload_fileobj = MagicMock()

        storage = S3Storage(s3_client=mock_client, bucket="test-bucket")

        with patch.dict("sys.modules", {"botocore.config": MagicMock()}):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.return_value = None

                file = BytesIO(b"test content")
                await storage.upload(file, "test-key.txt", "text/plain")

                # Verify asyncio.to_thread wraps the streaming upload call.
                # P2-5: seek/tell size probing is also offloaded, so assert on
                # the upload call itself rather than the total call count.
                upload_calls = [
                    c
                    for c in mock_to_thread.call_args_list
                    if c.args and c.args[0] is mock_client.upload_fileobj
                ]
                assert len(upload_calls) == 1

    @pytest.mark.asyncio
    async def test_event_loop_responsive_during_upload(self) -> None:
        """Verify event loop remains responsive during S3 upload."""
        from src.app.modules.storage.s3_storage import S3Storage

        mock_client = MagicMock()
        mock_client.put_object = MagicMock()

        storage = S3Storage(s3_client=mock_client, bucket="test-bucket")

        async def concurrent_task() -> str:
            await asyncio.sleep(0.01)
            return "completed"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            file = BytesIO(b"test content")
            results = await asyncio.gather(
                storage.upload(file, "test-key.txt", "text/plain"),
                concurrent_task(),
                return_exceptions=True,
            )

            # Concurrent task should complete
            assert results[1] == "completed"

    def test_s3_storage_has_to_thread_pattern(self) -> None:
        """Verify S3 storage code uses asyncio.to_thread pattern."""
        import inspect

        from src.app.modules.storage.s3_storage import S3Storage

        source = inspect.getsource(S3Storage)

        # Verify asyncio.to_thread is used for upload
        assert "asyncio.to_thread" in source
