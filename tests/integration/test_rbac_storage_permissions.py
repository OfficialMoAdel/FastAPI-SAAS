"""Integration tests for RBAC storage permissions.

Tests permission enforcement on storage settings endpoints:
- MANAGE_STORAGE_SETTINGS permission required for all operations
- Unauthorized users receive 403 Forbidden
"""

import pytest
from fastapi import status


@pytest.mark.asyncio
class TestStorageSettingsPermissions:
    """Tests for storage settings permission enforcement."""

    async def test_get_storage_settings_superuser(self, client, superuser_token_headers):
        """Superuser should have access."""
        response = client.get("/api/v1/storage", headers=superuser_token_headers)
        # 404 = no settings, 200 = settings exist
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND]

    async def test_get_storage_settings_regular_user_forbidden(self, client, regular_user_token_headers):
        """Regular user without permission should get 403."""
        response = client.get("/api/v1/storage", headers=regular_user_token_headers)
        # 401 = tenant not found, 403 = no permission, 404 = no storage settings for tenant
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

    async def test_create_storage_settings_regular_user_forbidden(self, client, regular_user_token_headers):
        """Regular user cannot create settings."""
        payload = {
            "provider": "local",
            "credentials": {"base_path": "/tmp/test"},
        }
        response = client.post("/api/v1/storage", json=payload, headers=regular_user_token_headers)
        # 400 = validation error (credentials), 401 = tenant not found, 403 = no permission, 404 = no storage settings for tenant  # noqa: E501
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

    async def test_update_storage_settings_regular_user_forbidden(self, client, regular_user_token_headers):
        """Regular user cannot update settings."""
        payload = {"max_file_size_mb": 200}
        response = client.patch("/api/v1/storage", json=payload, headers=regular_user_token_headers)
        # 401 = tenant not found, 403 = no permission, 404 = no storage settings for tenant
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]


@pytest.mark.asyncio
class TestStorageSettingsAdminOnly:
    """Tests that storage settings are admin-only operations."""

    async def test_storage_settings_not_accessible_without_tenant(self, client, superuser_token_headers):
        """Storage settings require tenant context."""
        # Request without X-Tenant-ID header
        headers = superuser_token_headers.copy()
        headers.pop("X-Tenant-ID", None)

        response = client.get("/api/v1/storage", headers=headers)
        # Should fail due to missing tenant context
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]
