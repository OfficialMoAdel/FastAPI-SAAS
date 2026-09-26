"""Direct tests for the storage provider registry seam.

Covers ``StorageProviderRegistry`` (register/unregister/reset/create) and the
``get_provider_for_settings`` instance cache. LocalStorage is used as the
stand-in provider throughout; S3/Azure adapters are intentionally untouched.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.clients.storage.interfaces import StorageProvider
from src.app.clients.storage.local_storage import LocalStorage
from src.app.clients.storage.registry import StorageProviderRegistry
from src.app.modules.storage.admin import _PROVIDER_CACHE
from src.app.modules.storage.models import StorageProviderType, StorageSettings
from src.app.modules.storage.service import StorageAdminService


@pytest.fixture(autouse=True)
def _isolated_registry_and_cache() -> Generator[None, None, None]:
    """Reset the process-global registry and provider cache around each test."""
    StorageProviderRegistry.reset()
    _PROVIDER_CACHE.clear()
    yield
    _PROVIDER_CACHE.clear()
    StorageProviderRegistry.reset()


def _local_settings(tenant_id: uuid.UUID, updated_at: datetime | None = None) -> StorageSettings:
    return StorageSettings(
        tenant_id=tenant_id,
        provider=StorageProviderType.LOCAL.value,
        updated_at=updated_at,
    )


class TestRegistryBuiltins:
    def test_reset_restores_all_three_builtins(self) -> None:
        StorageProviderRegistry.unregister(StorageProviderType.S3)
        StorageProviderRegistry.unregister(StorageProviderType.AZURE)
        StorageProviderRegistry.unregister(StorageProviderType.LOCAL)

        StorageProviderRegistry.reset()

        assert set(StorageProviderRegistry._factories) == {
            StorageProviderType.S3,
            StorageProviderType.AZURE,
            StorageProviderType.LOCAL,
        }

    def test_register_overrides_factory_and_create_uses_it(self, tmp_path: Path) -> None:
        sentinel = LocalStorage(base_path=str(tmp_path))

        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda _creds: sentinel)

        assert StorageProviderRegistry.create(StorageProviderType.LOCAL, {}) is sentinel

    def test_unregister_makes_create_raise_value_error(self) -> None:
        StorageProviderRegistry.unregister(StorageProviderType.LOCAL)

        with pytest.raises(ValueError, match="Unknown provider"):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"})

    def test_override_does_not_leak_after_reset(self, tmp_path: Path) -> None:
        sentinel = LocalStorage(base_path=str(tmp_path))
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda _creds: sentinel)

        StorageProviderRegistry.reset()

        provider = StorageProviderRegistry.create(
            StorageProviderType.LOCAL, {"base_path": str(tmp_path)}
        )
        assert provider is not sentinel
        assert isinstance(provider, LocalStorage)

    async def test_create_local_returns_working_provider(self, tmp_path: Path) -> None:
        provider = StorageProviderRegistry.create(
            StorageProviderType.LOCAL, {"base_path": str(tmp_path)}
        )

        assert isinstance(provider, StorageProvider)
        content = b"registry round-trip" * 64
        result = await provider.upload(io.BytesIO(content), "seam/file.bin", "application/octet-stream")
        assert result.success is True

        downloaded = b""
        async for chunk in provider.download("seam/file.bin"):
            downloaded += chunk
        assert downloaded == content


class TestGetProviderForSettingsCache:
    def _service(self) -> StorageAdminService:
        db = AsyncMock()
        db.add = MagicMock()
        return StorageAdminService(db)

    def test_same_settings_returns_same_provider_instance(self, tmp_path: Path) -> None:
        service = self._service()
        settings = _local_settings(uuid.uuid4(), datetime.now(UTC))

        with patch.object(
            service, "_decrypt_credentials", return_value={"base_path": str(tmp_path)}
        ) as mock_decrypt:
            first = service.get_provider_for_settings(settings)
            second = service.get_provider_for_settings(settings)

        assert first is second
        assert mock_decrypt.call_count == 1

    async def test_settings_change_returns_new_provider(self, tmp_path: Path) -> None:
        service = self._service()
        tenant_id = uuid.uuid4()
        before = _local_settings(tenant_id, datetime(2026, 1, 1, tzinfo=UTC))
        after = _local_settings(tenant_id, datetime(2026, 1, 2, tzinfo=UTC))

        other_dir = tmp_path / "rotated"
        other_dir.mkdir()
        with patch.object(
            service, "_decrypt_credentials", return_value={"base_path": str(tmp_path)}
        ):
            first = service.get_provider_for_settings(before)
        with patch.object(
            service,
            "_decrypt_credentials",
            return_value={"base_path": str(other_dir), "base_url": "/v2"},
        ):
            second = service.get_provider_for_settings(after)

        assert first is not second
        assert await second.get_url("f.txt") == "/v2/f.txt"
