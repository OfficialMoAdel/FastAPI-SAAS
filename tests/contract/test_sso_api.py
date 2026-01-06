"""Contract tests for SSO API endpoints.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T034 [US2] Write contract tests for /auth/sso/* endpoints

Tests verify the API contract per specs/004-auth-user-management/contracts/auth-api.yaml
"""

from unittest.mock import AsyncMock, Mock, patch

from fastapi import status


class TestSSOProvidersEndpoint:
    """Contract tests for GET /auth/sso/providers endpoint."""

    def test_providers_endpoint_exists(self, client, auth_headers):
        """Verify endpoint exists and accepts GET requests."""
        response = client.get(
            "/api/v1/auth/sso/providers",
            headers=auth_headers,
        )
        assert response.status_code not in [404, 405]

    def test_providers_endpoint_returns_list(self, client, auth_headers):
        """Verify endpoint returns a list of providers."""
        mock_providers = [
            {"name": "google", "enabled": True, "display_name": "Google"},
            {"name": "github", "enabled": True, "display_name": "GitHub"},
        ]
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(get_enabled_providers=AsyncMock(return_value=mock_providers)),
        ):
            response = client.get(
                "/api/v1/auth/sso/providers",
                headers=auth_headers,
            )
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "providers" in data
            assert isinstance(data["providers"], list)


class TestSSOAuthorizeEndpoint:
    """Contract tests for GET /auth/sso/{provider}/authorize endpoint."""

    def test_authorize_endpoint_exists(self, client, auth_headers):
        """Verify authorize endpoint exists."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(get_authorization_url=AsyncMock(return_value="https://google.com/auth")),
        ):
            response = client.get(
                "/api/v1/auth/sso/google/authorize",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code not in [404]

    def test_authorize_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.get(
            "/api/v1/auth/sso/unknown_provider/authorize",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_authorize_redirect_includes_state(self, client, auth_headers):
        """Verify authorize returns redirect URL with state parameter."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(
                get_authorization_url=AsyncMock(
                    return_value="https://accounts.google.com/o/oauth2/v2/auth?state=test_state&redirect_uri=https://example.com/callback"
                )
            ),
        ):
            response = client.get(
                "/api/v1/auth/sso/google/authorize",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
            assert "state=test_state" in response.headers["location"]


class TestSSOCallbackEndpoint:
    """Contract tests for GET /auth/sso/{provider}/callback endpoint."""

    def test_callback_endpoint_exists(self, client, auth_headers):
        """Verify callback endpoint exists."""
        response = client.get(
            "/api/v1/auth/sso/google/callback",
            headers=auth_headers,
        )
        assert response.status_code not in [404]

    def test_callback_requires_code(self, client, auth_headers):
        """Verify callback requires code parameter."""
        response = client.get(
            "/api/v1/auth/sso/google/callback",
            headers=auth_headers,
        )
        # FastAPI returns 422 for missing required query parameters
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY]

    def test_callback_requires_state(self, client, auth_headers):
        """Verify callback requires state parameter."""
        response = client.get(
            "/api/v1/auth/sso/google/callback?code=test_code",
            headers=auth_headers,
        )
        # FastAPI returns 422 for missing required query parameters
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY]

    def test_callback_invalid_state_returns_400(self, client, auth_headers):
        """Verify invalid state returns 400."""
        from fastapi import HTTPException
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(
                process_callback=AsyncMock(
                    side_effect=HTTPException(status_code=400, detail="Invalid state")
                )
            ),
        ):
            response = client.get(
                "/api/v1/auth/sso/google/callback?code=test_code&state=invalid_state",
                headers=auth_headers,
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_callback_valid_code_returns_tokens(self, client, auth_headers):
        """Verify valid callback returns tokens."""
        mock_tokens = {
            "access_token": "test_access_token",
            "refresh_token": "test_refresh_token",
            "token_type": "bearer",
            "expires_in": 1800,
        }
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(process_callback=AsyncMock(return_value=mock_tokens)),
        ):
            response = client.get(
                "/api/v1/auth/sso/google/callback?code=test_code&state=test_state",
                headers=auth_headers,
            )
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "access_token" in data
            assert "refresh_token" in data
            assert data["token_type"] == "bearer"


class TestSSOConnectEndpoint:
    """Contract tests for POST /auth/sso/{provider}/connect endpoint."""

    def test_connect_endpoint_exists(self, client, auth_headers):
        """Verify connect endpoint exists."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(get_authorization_url=AsyncMock(return_value="https://google.com/auth")),
        ):
            response = client.post(
                "/api/v1/auth/sso/google/connect",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code not in [404, 405]

    def test_connect_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.post(
            "/api/v1/auth/sso/unknown_provider/connect",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_connect_returns_authorization_url(self, client, auth_headers):
        """Verify connect returns authorization URL in Location header."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(
                get_authorization_url=AsyncMock(
                    return_value="https://accounts.google.com/o/oauth2/v2/auth?state=connect_state"
                )
            ),
        ):
            response = client.post(
                "/api/v1/auth/sso/google/connect",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
            assert "state=connect_state" in response.headers["location"]


class TestSSODisconnectEndpoint:
    """Contract tests for DELETE /auth/sso/{provider} endpoint."""

    def test_disconnect_endpoint_exists(self, client, auth_headers):
        """Verify disconnect endpoint exists."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(disconnect_provider=AsyncMock(return_value={"message": "ok"})),
        ):
            response = client.delete(
                "/api/v1/auth/sso/google",
                headers=auth_headers,
            )
            assert response.status_code not in [404, 405]

    def test_disconnect_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.delete(
            "/api/v1/auth/sso/unknown_provider",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_disconnect_returns_success_message(self, client, auth_headers):
        """Verify disconnect returns success message."""
        with patch(
            "src.app.modules.sso.router.sso_service",
            Mock(disconnect_provider=AsyncMock(return_value={"message": "Provider disconnected successfully"})),
        ):
            response = client.delete(
                "/api/v1/auth/sso/google",
                headers=auth_headers,
            )
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "message" in data
