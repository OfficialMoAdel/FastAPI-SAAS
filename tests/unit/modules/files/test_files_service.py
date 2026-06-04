import io
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.files.schemas import FileUpdate
from src.app.modules.files.service import (
    FileEntityNotFoundError,
    FileService,
    FileTypeNotAllowedError,
    QuotaExceededError,
)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_storage_service():
    service = MagicMock()
    service.get_settings_or_raise = AsyncMock()
    service.get_provider = AsyncMock()
    return service


@pytest.fixture
def file_service(mock_db, mock_storage_service):
    service = FileService(mock_db)
    service._storage_service = mock_storage_service
    return service


@pytest.mark.asyncio
async def test_upload_file_success(file_service, mock_db, mock_storage_service):
    tenant_id = uuid4()
    user_id = 1
    file_obj = io.BytesIO(b"test content")

    mock_settings = MagicMock()
    mock_settings.max_file_size_mb = 10
    mock_settings.allowed_file_types = ["text/plain"]
    mock_settings.max_file_count = 100
    mock_settings.storage_quota_bytes = 1024 * 1024 * 1024
    mock_settings.provider = "local"
    mock_storage_service.get_settings_or_raise.return_value = mock_settings

    # Mock quota stats
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.file_count = 5
    mock_row.storage_usage_bytes = 1000
    mock_result.one_or_none.return_value = mock_row
    mock_db.execute.return_value = mock_result

    mock_provider = MagicMock()
    mock_upload_result = MagicMock()
    mock_upload_result.success = True
    mock_provider.upload = AsyncMock(return_value=mock_upload_result)
    mock_storage_service.get_provider.return_value = mock_provider


    result = await file_service.upload_file(
        tenant_id=tenant_id, user_id=user_id, file=file_obj, filename="test.txt", mime_type="text/plain", size_bytes=100
    )

    assert result.original_name == "test.txt"
    assert result.mime_type == "text/plain"
    assert result.size_bytes == 100
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_provider.upload.assert_called_once()


@pytest.mark.asyncio
async def test_upload_file_size_exceeded(file_service, mock_storage_service):
    tenant_id = uuid4()
    mock_settings = MagicMock()
    mock_settings.max_file_size_mb = 1
    mock_storage_service.get_settings_or_raise.return_value = mock_settings

    with pytest.raises(QuotaExceededError, match="File size"):
        await file_service.upload_file(
            tenant_id=tenant_id,
            user_id=1,
            file=io.BytesIO(),
            filename="test.txt",
            mime_type="text/plain",
            size_bytes=2 * 1024 * 1024,
        )


@pytest.mark.asyncio
async def test_upload_file_type_not_allowed(file_service, mock_storage_service):
    tenant_id = uuid4()
    mock_settings = MagicMock()
    mock_settings.max_file_size_mb = 10
    mock_settings.allowed_file_types = ["image/png"]
    mock_storage_service.get_settings_or_raise.return_value = mock_settings

    with pytest.raises(FileTypeNotAllowedError, match="text/plain"):
        await file_service.upload_file(
            tenant_id=tenant_id,
            user_id=1,
            file=io.BytesIO(),
            filename="test.txt",
            mime_type="text/plain",
            size_bytes=100,
        )




@pytest.mark.asyncio
async def test_get_file(file_service, mock_db):
    tenant_id = uuid4()
    file_id = 1

    mock_file = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_file
    mock_db.execute.return_value = mock_result

    result = await file_service.get_file(tenant_id, file_id)
    assert result == mock_file


@pytest.mark.asyncio
async def test_get_file_or_raise(file_service, mock_db):
    tenant_id = uuid4()
    file_id = 1

    file_service.get_file = AsyncMock(return_value=None)
    with pytest.raises(FileEntityNotFoundError):
        await file_service.get_file_or_raise(tenant_id, file_id)

    mock_file = MagicMock()
    file_service.get_file = AsyncMock(return_value=mock_file)
    assert await file_service.get_file_or_raise(tenant_id, file_id) == mock_file


@pytest.mark.asyncio
async def test_list_files(file_service, mock_db):
    tenant_id = uuid4()

    # Needs to mock _get_quota_stats and db.execute
    file_service._get_quota_stats = AsyncMock(return_value=(10, 1000))

    mock_file = MagicMock()
    mock_file.id = 1
    mock_file.size_bytes = 100
    # Provide necessary attributes for FileRead schema
    mock_file.tenant_id = uuid4()
    mock_file.user_id = 1
    mock_file.original_name = "test.txt"
    mock_file.mime_type = "text/plain"
    mock_file.storage_path = "path/to/test.txt"
    mock_file.is_public = False
    mock_file.description = None
    mock_file.created_at = datetime.now()
    mock_file.updated_at = datetime.now()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_file]
    mock_db.execute.return_value = mock_result

    result = await file_service.list_files(tenant_id=tenant_id)

    assert result["total_count"] == 10
    assert len(result["data"]) == 1
    assert result["data"][0].id == 1


@pytest.mark.asyncio
async def test_download_file(file_service, mock_storage_service):
    tenant_id = uuid4()
    file_id = 1

    mock_file = MagicMock()
    mock_file.storage_path = "path"
    mock_file.size_bytes = 100
    file_service.get_file_or_raise = AsyncMock(return_value=mock_file)

    mock_settings = MagicMock()
    mock_settings.provider = "local"
    mock_storage_service.get_settings_or_raise.return_value = mock_settings

    mock_provider = MagicMock()

    async def mock_download_gen(*args, **kwargs):
        yield b"chunk1"
        yield b"chunk2"

    mock_provider.download = mock_download_gen
    mock_storage_service.get_provider.return_value = mock_provider

    chunks = []
    async for chunk in file_service.download_file(tenant_id, file_id):
        chunks.append(chunk)

    assert chunks == [b"chunk1", b"chunk2"]


@pytest.mark.asyncio
async def test_update_file(file_service, mock_db):
    tenant_id = uuid4()
    file_id = 1

    mock_file = MagicMock()
    file_service.get_file_or_raise = AsyncMock(return_value=mock_file)

    update_data = FileUpdate(original_name="new.txt", is_public=True, description="desc")

    await file_service.update_file(tenant_id, file_id, update_data)

    assert mock_file.original_name == "new.txt"
    assert mock_file.is_public is True
    assert mock_file.description == "desc"
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_delete_file(file_service, mock_db, mock_storage_service):
    tenant_id = uuid4()
    file_id = 1

    mock_file = MagicMock()
    mock_file.size_bytes = 100
    mock_file.storage_path = "path/to/file"
    file_service.get_file_or_raise = AsyncMock(return_value=mock_file)
    file_service._decrement_quota_counters = AsyncMock()

    mock_provider = MagicMock()
    mock_provider.delete = AsyncMock()
    mock_storage_service.get_provider.return_value = mock_provider

    mock_settings = MagicMock()
    mock_settings.provider = "local"
    mock_storage_service.get_settings_or_raise.return_value = mock_settings

    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_db.execute.return_value = mock_result

    await file_service.delete_file(tenant_id, file_id)

    # We now assert that execute was called (for the explicit delete statement)
    mock_db.execute.assert_called_once()
    file_service._decrement_quota_counters.assert_called_once_with(tenant_id, 100)
    mock_db.commit.assert_called_once()
    mock_provider.delete.assert_called_once_with("path/to/file")


@pytest.mark.asyncio
async def test_get_signed_url(file_service, mock_storage_service):
    tenant_id = uuid4()
    file_id = 1

    mock_file = MagicMock()
    mock_file.storage_path = "path"
    file_service.get_file_or_raise = AsyncMock(return_value=mock_file)

    mock_provider = MagicMock()
    mock_provider.get_url = AsyncMock(return_value="https://signed.url")
    mock_storage_service.get_provider.return_value = mock_provider

    url = await file_service.get_signed_url(tenant_id, file_id, 3600)

    assert url == "https://signed.url"
    mock_provider.get_url.assert_called_once_with("path", 3600)


@pytest.mark.asyncio
async def test_transfer_ownership(file_service, mock_db):
    tenant_id = uuid4()

    mock_result = MagicMock()
    mock_result.rowcount = 5
    mock_db.execute.return_value = mock_result

    count = await file_service.transfer_ownership(tenant_id, from_user_id=1, to_user_id=2)

    assert count == 5
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_file_count_total_size(file_service, mock_db):
    # Test deprecated methods for coverage
    tenant_id = uuid4()

    mock_result = MagicMock()
    mock_result.scalar.return_value = 10
    mock_db.execute.return_value = mock_result

    count = await file_service._get_file_count(tenant_id)
    assert count == 10

    mock_result2 = MagicMock()
    mock_result2.scalar.return_value = 1000
    mock_db.execute.return_value = mock_result2

    size = await file_service._get_total_size(tenant_id)
    assert size == 1000
