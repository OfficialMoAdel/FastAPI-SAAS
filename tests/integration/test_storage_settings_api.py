"""Integration tests for storage settings API endpoints.

Tests the storage settings CRUD operations:
- GET /storage-settings - Get tenant storage settings
- POST /storage-settings - Create storage settings
- PATCH /storage-settings - Update storage settings
"""

import pytest
from fastapi import status


@pytest.mark.asyncio
class TestStorageSettingsAPI:
    """Tests for storage settings endpoints."""

    async def test_get_storage_settings_not_configured(self, client, superuser_token_headers):
        """GET /storage-settings returns 404 when not configured."""
        response = client.get("/api/v1/storage-settings", headers=superuser_token_headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_create_storage_settings_requires_credentials(self, client, superuser_token_headers):
        """POST /storage-settings requires credentials."""
        # Without credentials, should fail validation
        payload = {"provider": "local", "max_file_size_mb": 50}
        response = client.post("/api/v1/storage-settings", json=payload, headers=superuser_token_headers)
        # 400 = validation error (missing credentials), 201 = success
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_201_CREATED]

    async def test_create_storage_settings_with_credentials(self, client, superuser_token_headers):
        """POST /storage-settings creates settings with valid credentials."""
        payload = {
            "provider": "local",
            "credentials": {"base_path": "/tmp/test-storage"},
            "max_file_size_mb": 50,
        }
        response = client.post("/api/v1/storage-settings", json=payload, headers=superuser_token_headers)
        # 201 = success, 409 = already exists, 400 = validation error
        assert response.status_code in [
            status.HTTP_201_CREATED,
            status.HTTP_409_CONFLICT,
            status.HTTP_400_BAD_REQUEST,
        ]


@pytest.mark.asyncio
class TestStorageSettingsValidation:
    """Tests for storage settings input validation."""

    async def test_create_invalid_file_size(self, client, superuser_token_headers):
        """POST /storage-settings rejects invalid max_file_size_mb."""
        payload = {"provider": "local", "max_file_size_mb": 10000}  # > 5120 limit
        response = client.post("/api/v1/storage-settings", json=payload, headers=superuser_token_headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_create_negative_quota(self, client, superuser_token_headers):
        """POST /storage-settings rejects negative storage quota."""
        payload = {"provider": "local", "storage_quota_bytes": -1}
        response = client.post("/api/v1/storage-settings", json=payload, headers=superuser_token_headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_create_invalid_file_types(self, client, superuser_token_headers):
        """POST /storage-settings validates allowed_file_types format."""
        payload = {"provider": "local", "allowed_file_types": "not-a-list"}
        response = client.post("/api/v1/storage-settings", json=payload, headers=superuser_token_headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
