import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from src.app.api.deps.request_context import RequestContext
from src.app.clients.storage.local_storage import CHUNK_SIZE, LocalStorage
from src.app.clients.storage.s3_storage import S3Storage
from src.app.core.tenancy.context import TenantContext
from src.app.modules.storage.routers.files import upload_file


@pytest.mark.asyncio
async def test_router_upload_file_optimization():
    """Verify upload_file uses seek/tell instead of reading content."""
    # Mock UploadFile
    mock_file = MagicMock(spec=UploadFile)
    mock_file.filename = "test.txt"
    mock_file.size = None
    mock_file.file = MagicMock()
    mock_file.file.tell.return_value = 12345
    # Make file.file.seek sync (SpooledTemporaryFile uses sync seek)
    mock_file.file.seek = MagicMock()
    mock_file.read = AsyncMock()  # Should NOT be called

    # Mock dependencies
    mock_tenant_id = uuid.uuid4()
    tenant_ctx = TenantContext(
        tenant_id=mock_tenant_id,
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )
    ctx = RequestContext(
        user=MagicMock(id=12345),  # AuthenticatedUser-like; only .id accessed via get_user_attr
        tenant=tenant_ctx,
        db=MagicMock(),
    )
    mock_service = AsyncMock()
    # Mock service result
    mock_result = MagicMock()
    mock_result.id = 123
    mock_result.original_name = "test.txt"
    mock_result.storage_path = "/tmp/test.txt"
    mock_result.mime_type = "text/plain"
    mock_result.size_bytes = 100
    mock_result.is_public = False
    mock_result.created_at = "2023-01-01T00:00:00Z"
    mock_service.upload_file.return_value = mock_result

    with patch("src.app.modules.storage.routers.files.get_storage", return_value=mock_service):
        await upload_file(
            file=mock_file,
            ctx=ctx,
            service=mock_service,
        )

    # Verify seek/tell logic used (file.file.seek is the sync method used by router)
    # Router uses: file.file.seek(0, 2), file.file.tell(), file.file.seek(0)
    mock_file.file.seek.assert_any_call(0, 2)
    mock_file.file.tell.assert_called_once()
    mock_file.file.seek.assert_any_call(0, 0)

    # Verify read NOT called
    mock_file.read.assert_not_called()


@pytest.mark.asyncio
async def test_local_storage_streaming():
    """Verify LocalStorage.upload streams content."""
    provider = LocalStorage(base_path="/tmp/test_storage")  # Changed root_path to base_path

    # Mock file object (sync binary IO)
    mock_file = MagicMock()
    # read side_effect returns chunks then empty bytes to signal EOF
    mock_file.read.side_effect = [b"chunk1", b"chunk2", b""]

    # Mock aiofiles context manager
    mock_f = AsyncMock()
    mock_opener = MagicMock()
    mock_opener.__aenter__.return_value = mock_f

    with (
        patch("aiofiles.open", return_value=mock_opener),
        patch("aiofiles.os.makedirs", new_callable=AsyncMock),
    ):
        await provider.upload(mock_file, "test/file.txt", "text/plain")

        # Verify write called for each chunk (2) + metadata (1)
        assert mock_f.write.call_count == 3
        mock_f.write.assert_any_call(b"chunk1")
        mock_f.write.assert_any_call(b"chunk2")
        mock_f.write.assert_any_call("text/plain")

        # Verify read called with CHUNK_SIZE (proving streaming)
        mock_file.read.assert_called_with(CHUNK_SIZE)


@pytest.mark.asyncio
async def test_s3_storage_streaming():
    """Verify S3Storage.upload uses streaming (the only path since P-M8)."""
    # Mock botocore
    with patch.dict("sys.modules", {"botocore.config": MagicMock()}):
        # Mock S3 client
        mock_client = MagicMock()
        # Mock upload_fileobj (it's called in a thread)
        mock_client.upload_fileobj = MagicMock()

        provider = S3Storage(s3_client=mock_client, bucket="test-bucket")

        mock_file = MagicMock()
        mock_file.size = 12345
        # seek is checked for reset
        mock_file.seek = MagicMock()

        result = await provider.upload(mock_file, "test/file.txt", "text/plain")

        # Verify success
        assert result.success, f"Upload failed: {result.error}"

        # Verify upload_fileobj called
        mock_client.upload_fileobj.assert_called_once()
        # Verify put_object NOT called
        assert not hasattr(mock_client, "put_object") or not mock_client.put_object.called


@pytest.mark.asyncio
async def test_s3_storage_legacy():
    """P-M8: upload always uses the streaming path — the legacy spooled-temp
    fallback was deleted (B13: the FEATURE_STREAMING_UPLOADS flag is gone)."""
    with patch.dict("sys.modules", {"botocore.config": MagicMock()}):
        mock_client = MagicMock()
        mock_client.upload_fileobj = MagicMock()
        mock_client.put_object = MagicMock()

        provider = S3Storage(s3_client=mock_client, bucket="test-bucket")

        mock_file = MagicMock()
        mock_file.size = 12345
        mock_file.seek = MagicMock()

        result = await provider.upload(mock_file, "test/file.txt", "text/plain")

        assert result.success, f"Upload failed: {result.error}"
        mock_client.upload_fileobj.assert_called_once()
        mock_client.put_object.assert_not_called()
