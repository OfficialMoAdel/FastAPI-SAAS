"""Unit tests for AzureBlobStorage provider with mocked Azure clients."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.storage.azure_storage import AzureBlobStorage
from src.app.modules.storage.interfaces import StorageResult

CONN = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc123==;EndpointSuffix=core.windows.net"


def _make_storage() -> AzureBlobStorage:
    return AzureBlobStorage(connection_string=CONN, container_name="files")


async def _async_iter(items):
    for item in items:
        yield item


def test_parse_account_name() -> None:
    assert AzureBlobStorage._parse_account_name(CONN) == "myaccount"


def test_parse_account_key() -> None:
    assert AzureBlobStorage._parse_account_key(CONN) == "abc123=="


def test_parse_account_name_missing_raises() -> None:
    with pytest.raises(ValueError):
        AzureBlobStorage._parse_account_name("NoAccountHere=1")


def test_parse_account_key_missing_raises() -> None:
    with pytest.raises(ValueError):
        AzureBlobStorage._parse_account_key("NoKeyHere=1")


@pytest.fixture
def mocked_storage():
    storage = _make_storage()
    container = MagicMock()
    blob_client = MagicMock()
    blob_client.upload_blob = AsyncMock()
    blob_client.delete_blob = AsyncMock()
    blob_client.get_blob_properties = AsyncMock()
    blob_client.download_blob = AsyncMock(
        return_value=MagicMock(chunks=MagicMock(return_value=_async_iter([b"chunk1", b"chunk2"])))
    )
    blob_client.url = "https://myaccount.blob.core.windows.net/files/path"
    container.get_blob_client = MagicMock(return_value=blob_client)
    container.get_container_properties = AsyncMock()
    service_client = MagicMock()
    service_client.get_container_client = MagicMock(return_value=container)
    service_client.close = AsyncMock()
    with patch.object(storage, "_get_container_client", AsyncMock(return_value=container)):
        storage._service_client = service_client
        yield storage, blob_client


async def test_upload_success(mocked_storage) -> None:
    storage, blob_client = mocked_storage
    f = io.BytesIO(b"data" * 100)
    result = await storage.upload(f, "path/blob.txt", "text/plain")
    assert isinstance(result, StorageResult)
    assert result.success is True
    blob_client.upload_blob.assert_awaited_once()


async def test_upload_failure_returns_error(mocked_storage) -> None:
    storage, blob_client = mocked_storage
    blob_client.upload_blob.side_effect = RuntimeError("boom")
    f = io.BytesIO(b"data")
    result = await storage.upload(f, "path/blob.txt", "text/plain")
    assert result.success is False
    assert "boom" in result.error


async def test_download_yields_chunks(mocked_storage) -> None:
    storage, _ = mocked_storage
    chunks = []
    async for c in storage.download("path/blob.txt"):
        chunks.append(c)
    assert chunks == [b"chunk1", b"chunk2"]


async def test_delete_success(mocked_storage) -> None:
    storage, blob_client = mocked_storage
    result = await storage.delete("path/blob.txt")
    assert result.success is True
    blob_client.delete_blob.assert_awaited_once()


async def test_delete_not_found_idempotent(mocked_storage) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    storage, blob_client = mocked_storage
    blob_client.delete_blob.side_effect = ResourceNotFoundError("missing")
    result = await storage.delete("path/blob.txt")
    assert result.success is True


async def test_delete_generic_error(mocked_storage) -> None:
    storage, blob_client = mocked_storage
    blob_client.delete_blob.side_effect = RuntimeError("fail")
    result = await storage.delete("path/blob.txt")
    assert result.success is False


async def test_exists_true(mocked_storage) -> None:
    storage, _ = mocked_storage
    assert await storage.exists("path/blob.txt") is True


async def test_exists_false(mocked_storage) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    storage, blob_client = mocked_storage
    blob_client.get_blob_properties.side_effect = ResourceNotFoundError("missing")
    assert await storage.exists("path/blob.txt") is False


async def test_get_url_with_sas(mocked_storage) -> None:
    storage, _ = mocked_storage
    with patch("src.app.modules.storage.azure_storage.generate_blob_sas", return_value="sig=token"):
        url = await storage.get_url("path/blob.txt", expires_in=600)
    assert url.startswith("https://myaccount.blob.core.windows.net/files/path")
    assert url.endswith("?sig=token")


async def test_validate_connection_success(mocked_storage) -> None:
    storage, _ = mocked_storage
    assert await storage.validate_connection() is True


async def test_validate_connection_failure(mocked_storage) -> None:
    storage, _ = mocked_storage
    storage._get_container_client = AsyncMock(side_effect=RuntimeError("conn fail"))
    assert await storage.validate_connection() is False


async def test_close(mocked_storage) -> None:
    storage, _ = mocked_storage
    await storage.close()
    assert storage._service_client is None
