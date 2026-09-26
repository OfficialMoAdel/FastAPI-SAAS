"""Unit tests for LocalStorage provider using a real temporary directory."""

from __future__ import annotations

import io

import pytest

from src.app.clients.storage.interfaces import StorageResult
from src.app.clients.storage.local_storage import LocalStorage


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(base_path=str(tmp_path), base_url="/files")


async def test_upload_and_download_roundtrip(storage: LocalStorage) -> None:
    content = b"hello world" * 100
    f = io.BytesIO(content)
    result = await storage.upload(f, "docs/file.txt", "text/plain")
    assert isinstance(result, StorageResult)
    assert result.success is True
    assert result.size == len(content)

    downloaded = b""
    async for chunk in storage.download("docs/file.txt"):
        downloaded += chunk
    assert downloaded == content


async def test_upload_creates_metadata(storage: LocalStorage, tmp_path) -> None:
    f = io.BytesIO(b"data")
    await storage.upload(f, "a/b.bin", "application/octet-stream")
    meta = tmp_path / "a" / "b.bin.meta"
    assert meta.exists()
    assert meta.read_text() == "application/octet-stream"


async def test_delete_existing(storage: LocalStorage) -> None:
    f = io.BytesIO(b"data")
    await storage.upload(f, "to_delete.txt", "text/plain")
    result = await storage.delete("to_delete.txt")
    assert result.success is True
    assert not await storage.exists("to_delete.txt")


async def test_delete_nonexistent_returns_success(storage: LocalStorage) -> None:
    result = await storage.delete("missing.txt")
    assert result.success is True


async def test_exists_true_and_false(storage: LocalStorage) -> None:
    f = io.BytesIO(b"data")
    await storage.upload(f, "present.txt", "text/plain")
    assert await storage.exists("present.txt") is True
    assert await storage.exists("absent.txt") is False


async def test_get_url(storage: LocalStorage) -> None:
    url = await storage.get_url("path/to/file.pdf")
    assert url == "/files/path/to/file.pdf"


async def test_get_mime_type(storage: LocalStorage) -> None:
    f = io.BytesIO(b"data")
    await storage.upload(f, "m.txt", "text/markdown")
    assert await storage.get_mime_type("m.txt") == "text/markdown"
    assert await storage.get_mime_type("nope.txt") is None


async def test_validate_connection(storage: LocalStorage) -> None:
    assert await storage.validate_connection() is True


async def test_path_traversal_rejected(storage: LocalStorage) -> None:
    f = io.BytesIO(b"evil")
    result = await storage.upload(f, "../../escape.txt", "text/plain")
    assert result.success is False
    assert "traversal" in result.error


async def test_download_missing_raises(storage: LocalStorage) -> None:
    with pytest.raises(Exception):
        async for _ in storage.download("nope.txt"):
            pass
