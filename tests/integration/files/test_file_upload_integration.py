import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.app.modules.storage_settings.service import StorageSettingsService
from src.app.modules.storage_settings.schemas import (
    StorageSettingsCreate,
    StorageProviderType,
    LocalCredentials,
)


@pytest.fixture
def mock_redis_lifespan():
    """Mock Redis lifespan events and cache client."""
    mock_redis = AsyncMock()
    mock_redis.exists.return_value = 0
    mock_redis.get.return_value = None
    
    # Mock encryption to avoid Fernet key issues
    with patch("src.app.core.setup.create_redis_queue_pool", new_callable=AsyncMock), \
         patch("src.app.core.setup.close_redis_cache_pool", new_callable=AsyncMock), \
         patch("src.app.core.setup.close_redis_queue_pool", new_callable=AsyncMock), \
         patch("src.app.core.setup.create_redis_cache_pool", new_callable=AsyncMock), \
         patch("src.app.core.utils.cache.client", mock_redis), \
         patch("src.app.modules.storage_settings.service.encrypt_value", side_effect=lambda x: f"enc_{x}".encode("utf-8")), \
         patch("src.app.modules.storage_settings.service.decrypt_value", side_effect=lambda x: x.decode("utf-8").removeprefix("enc_")):
        yield


@pytest.mark.asyncio
async def test_file_upload_integration(
    mock_redis_lifespan,
    client,
    db_session,
    tenant,
    create_test_user,
    tmp_path,
):
    """
    E2E test for file upload using LocalStorage.
    Verifies:
    1. Storage settings configuration.
    2. File upload via API.
    3. File existence on disk (streaming verify).
    4. Metadata file existence.
    """
    # 1. Configure Local Storage for the tenant
    settings_service = StorageSettingsService(db_session)

    # Create local credentials pointing to tmp_path
    local_creds = LocalCredentials(base_path=str(tmp_path), base_url="/files")

    settings_data = StorageSettingsCreate(
        provider=StorageProviderType.LOCAL,
        local_credentials=local_creds,
        max_file_size_mb=10,
        allowed_file_types=["text/plain"],
        max_file_count=100,
        storage_quota_bytes=1024 * 1024 * 100,
    )

    await settings_service.create_settings(tenant.id, settings_data)

    # 2. Upload a file
    file_content = b"Hello World form Integration Test"
    files = {"file": ("test.txt", file_content, "text/plain")}

    # Client is already authenticated as superuser by the 'client' fixture in conftest.py
    # We must provide X-Tenant-ID header for TenantMiddleware
    headers = {"X-Tenant-ID": tenant.slug}
    response = client.post("/api/v1/files/upload", files=files, headers=headers)
    
    assert response.status_code == 201, f"Upload failed: {response.text}"
    data = response.json()

    assert data["original_name"] == "test.txt"
    assert data["mime_type"] == "text/plain"

    # 3. Verify file on disk
    # LocalStorage structure: base_path / tenant_id / ...
    # logic in local_storage.py: full_path = self._get_full_path(path)
    # path returned by upload is usually "YYYY/MM/DD/uuid.ext"

    storage_path = data["storage_path"]
    # storage_path in DB is relative to base_path?
    # service.py upload logic: path = f"{year}/{month}/{day}/{uuid}{ext}"
    # LocalStorage.upload(..., path)
    # LocalStorage._get_full_path(path) = base_path / path

    # Wait, local_storage.py might prepend tenant_id?
    # Let's check local_storage.py logic again.
    # It takes base_path in init.
    # _get_full_path: return self.base_path / path

    # So full path is tmp_path / storage_path
    full_path = tmp_path / storage_path

    assert full_path.exists()
    assert full_path.read_bytes() == file_content

    # 4. Verify metadata
    meta_path = full_path.with_suffix(full_path.suffix + ".meta")
    assert meta_path.exists()
    assert meta_path.read_text() == "text/plain"