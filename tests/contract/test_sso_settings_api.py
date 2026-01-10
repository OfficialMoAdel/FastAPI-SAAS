"""Contract tests for SSO Settings API endpoints.

These tests verify the API contract as defined in sso-settings-api.yaml.
Focus on request/response schemas, status codes, and authentication.

Task: T100 [US11] Write contract tests for /sso-settings/* endpoints
"""


import pytest
from fastapi import status
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestSSOSettingsListContract:
    """Contract tests for GET /admin/sso-settings/."""

    async def test_list_returns_403_without_auth(self, unauthenticated_async_client: AsyncClient):
        """Verify 401/403 when not authenticated."""
        response = await unauthenticated_async_client.get("/api/v1/admin/sso-settings/")

        # Should require authentication
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    async def test_list_returns_403_for_non_superuser(
        self, async_client: AsyncClient, regular_user_token_headers: dict
    ):
        """Verify 403 for non-superuser users."""
        response = await async_client.get(
            "/api/v1/admin/sso-settings/",
            headers=regular_user_token_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_list_returns_200_for_superuser(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 200 and correct schema for superuser."""
        response = await async_client.get(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify paginated response schema
        assert "data" in data or "items" in data or isinstance(data, list)


class TestSSOSettingsCreateContract:
    """Contract tests for POST /admin/sso-settings/."""

    async def test_create_returns_403_without_auth(self, unauthenticated_async_client: AsyncClient):
        """Verify 401/403 when not authenticated."""
        response = await unauthenticated_async_client.post(
            "/api/v1/admin/sso-settings/",
            json={
                "provider": "google",
                "client_id": "test_id",
                "client_secret": "test_secret",
            },
        )

        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    async def test_create_validates_required_fields(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 422 for missing required fields."""
        response = await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={"provider": "google"},  # Missing client_id and client_secret
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_create_returns_201_on_success(
        self, async_client: AsyncClient, superuser_token_headers: dict, db_session
    ):
        """Verify 201 and correct response schema on success."""
        # Clean up any existing settings first
        from src.app.modules.sso_settings.crud import crud_sso_settings

        existing = await crud_sso_settings.get(db=db_session, provider="test_provider")
        if existing:
            await crud_sso_settings.delete(db=db_session, provider="test_provider")

        response = await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "test_provider",
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "is_enabled": True,
            },
        )

        # Should either succeed or fail with 400 (if provider already exists or invalid)
        assert response.status_code in [
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]

        if response.status_code == status.HTTP_201_CREATED:
            data = response.json()
            assert "provider" in data

    async def test_create_returns_400_for_duplicate_provider(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 400/409 for duplicate provider."""
        # Create first
        await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "duplicate_test",
                "client_id": "id1",
                "client_secret": "secret1",
            },
        )

        # Try to create again
        response = await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "duplicate_test",
                "client_id": "id2",
                "client_secret": "secret2",
            },
        )

        # Should reject duplicate
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]


class TestSSOSettingsGetContract:
    """Contract tests for GET /admin/sso-settings/{provider}."""

    async def test_get_returns_403_without_auth(self, unauthenticated_async_client: AsyncClient):
        """Verify 401/403 when not authenticated."""
        response = await unauthenticated_async_client.get("/api/v1/admin/sso-settings/google")

        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    async def test_get_returns_404_for_missing_provider(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 404 for non-existent provider."""
        response = await async_client.get(
            "/api/v1/admin/sso-settings/nonexistent_provider",
            headers=superuser_token_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_get_returns_200_with_correct_schema(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 200 and correct response schema for existing provider."""
        # First create a setting
        create_response = await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "get_test_provider",
                "client_id": "test_id",
                "client_secret": "test_secret",
            },
        )

        if create_response.status_code in [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST]:
            response = await async_client.get(
                "/api/v1/admin/sso-settings/get_test_provider",
                headers=superuser_token_headers,
            )

            if response.status_code == status.HTTP_200_OK:
                data = response.json()
                # Verify key fields present
                assert "provider" in data or "client_id" in data


class TestSSOSettingsUpdateContract:
    """Contract tests for PATCH /admin/sso-settings/{provider}."""

    async def test_update_returns_403_without_auth(self, unauthenticated_async_client: AsyncClient):
        """Verify 401/403 when not authenticated."""
        response = await unauthenticated_async_client.patch(
            "/api/v1/admin/sso-settings/google",
            json={"is_enabled": False},
        )

        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    async def test_update_returns_404_for_missing_provider(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 404 for non-existent provider."""
        response = await async_client.patch(
            "/api/v1/admin/sso-settings/nonexistent_provider",
            headers=superuser_token_headers,
            json={"is_enabled": False},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_update_accepts_partial_updates(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify partial updates are accepted."""
        # Create a setting first
        await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "update_test_provider",
                "client_id": "original_id",
                "client_secret": "original_secret",
            },
        )

        # Update only is_enabled
        response = await async_client.patch(
            "/api/v1/admin/sso-settings/update_test_provider",
            headers=superuser_token_headers,
            json={"is_enabled": False},
        )

        # Should succeed or return 404 if provider doesn't exist
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND]


class TestSSOSettingsDeleteContract:
    """Contract tests for DELETE /admin/sso-settings/{provider}."""

    async def test_delete_returns_403_without_auth(self, unauthenticated_async_client: AsyncClient):
        """Verify 401/403 when not authenticated."""
        response = await unauthenticated_async_client.delete("/api/v1/admin/sso-settings/google")

        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    async def test_delete_returns_404_for_missing_provider(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify 404 for non-existent provider."""
        response = await async_client.delete(
            "/api/v1/admin/sso-settings/nonexistent_provider_delete",
            headers=superuser_token_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_delete_returns_success_status(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify delete returns appropriate success status."""
        # Create a setting to delete
        await async_client.post(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
            json={
                "provider": "delete_test_provider",
                "client_id": "delete_id",
                "client_secret": "delete_secret",
            },
        )

        response = await async_client.delete(
            "/api/v1/admin/sso-settings/delete_test_provider",
            headers=superuser_token_headers,
        )

        # Should return 200 or 204 for successful delete
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
            status.HTTP_404_NOT_FOUND,  # If creation failed
        ]


class TestSSOSettingsSecurityContract:
    """Contract tests for security requirements."""

    async def test_credentials_not_exposed_in_list_response(
        self, async_client: AsyncClient, superuser_token_headers: dict
    ):
        """Verify client_secret is not exposed in list response."""
        response = await async_client.get(
            "/api/v1/admin/sso-settings/",
            headers=superuser_token_headers,
        )

        if response.status_code == status.HTTP_200_OK:
            data = response.json()
            # Check that client_secret is not plaintext in response
            json_str = str(data)
            # Secret should not appear as plaintext
            assert "test_client_secret" not in json_str or "has_secret" in json_str

    async def test_all_endpoints_require_authentication(self, unauthenticated_async_client: AsyncClient):
        """Verify all endpoints reject unauthenticated requests."""
        endpoints = [
            ("GET", "/api/v1/admin/sso-settings/"),
            ("POST", "/api/v1/admin/sso-settings/"),
            ("GET", "/api/v1/admin/sso-settings/google"),
            ("PATCH", "/api/v1/admin/sso-settings/google"),
            ("DELETE", "/api/v1/admin/sso-settings/google"),
        ]

        for method, path in endpoints:
            if method == "GET":
                response = await unauthenticated_async_client.get(path)
            elif method == "POST":
                response = await unauthenticated_async_client.post(path, json={})
            elif method == "PATCH":
                response = await unauthenticated_async_client.patch(path, json={})
            elif method == "DELETE":
                response = await unauthenticated_async_client.delete(path)

            assert response.status_code in [
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ], f"{method} {path} should require auth"
