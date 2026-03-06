"""Contract tests for SSO API endpoints.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T034 [US2] Write contract tests for /auth/sso/* endpoints

Tests verify the API contract per specs/004-auth-user-management/contracts/auth-api.yaml
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

from fastapi import status

from src.app.main import app
from src.app.modules.sso.service import get_sso_service


def _create_mock_sso_service(**method_returns):
    """Helper to create a mock SSOService with specified method return values."""
    mock_service = Mock()
    for method_name, return_value in method_returns.items():
        if isinstance(return_value, Exception):
            setattr(mock_service, method_name, AsyncMock(side_effect=return_value))
        else:
            setattr(mock_service, method_name, AsyncMock(return_value=return_value))
    return mock_service


@contextmanager
def override_sso_service(mock_service):
    """Context manager to override get_sso_service dependency."""
    app.dependency_overrides[get_sso_service] = lambda: mock_service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_sso_service, None)


class TestSSOProvidersEndpoint:
    """Contract tests for GET /auth/sso/providers endpoint."""

    def test_providers_endpoint_exists(self, client, auth_headers):
        """Verify endpoint exists and accepts GET requests."""
        response = client.get(
            "/api/v1/account/sso/providers",
            headers=auth_headers,
        )
        assert response.status_code not in [404, 405]

    def test_providers_endpoint_returns_list(self, client, auth_headers):
        """Verify endpoint returns a list of providers."""
        mock_providers = [
            {"name": "google", "enabled": True, "display_name": "Google"},
            {"name": "github", "enabled": True, "display_name": "GitHub"},
        ]
        mock_service = _create_mock_sso_service(get_enabled_providers=mock_providers)
        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/providers",
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
        mock_service = _create_mock_sso_service(get_authorization_url=("https://google.com/auth", "test_state"))
        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/authorize",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code not in [404]

    def test_authorize_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.get(
            "/api/v1/account/sso/unknown_provider/authorize",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_authorize_redirect_includes_state(self, client, auth_headers):
        """Verify authorize returns redirect URL with state parameter."""
        mock_service = _create_mock_sso_service(
            get_authorization_url=("https://accounts.google.com/o/oauth2/v2/auth?state=test_state&redirect_uri=https://example.com/callback", "test_state")
        )
        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/authorize",
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
            "/api/v1/account/sso/google/callback",
            headers=auth_headers,
        )
        assert response.status_code not in [404]

    def test_callback_requires_code(self, client, auth_headers):
        """Verify callback requires code parameter."""
        response = client.get(
            "/api/v1/account/sso/google/callback",
            headers=auth_headers,
        )
        # FastAPI returns 422 for missing required query parameters
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY]

    def test_callback_requires_state(self, client, auth_headers):
        """Verify callback requires state parameter."""
        response = client.get(
            "/api/v1/account/sso/google/callback?code=test_code",
            headers=auth_headers,
        )
        # FastAPI returns 422 for missing required query parameters
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY]

    def test_callback_invalid_state_returns_400(self, client, auth_headers):
        """Verify invalid state returns 400."""
        from fastapi import HTTPException

        mock_service = _create_mock_sso_service(process_callback=HTTPException(status_code=400, detail="Invalid state"))
        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=test_code&state=invalid_state",
                headers=auth_headers,
                cookies={"sso_state": "test_state"},
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
        mock_service = _create_mock_sso_service(process_callback=mock_tokens)
        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=test_code&state=test_state",
                headers=auth_headers,
                cookies={"sso_state": "test_state"},
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
        mock_service = _create_mock_sso_service(get_authorization_url=("https://google.com/auth", "test_state"))
        with override_sso_service(mock_service):
            response = client.post(
                "/api/v1/account/sso/google/connect",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code not in [404, 405]

    def test_connect_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.post(
            "/api/v1/account/sso/unknown_provider/connect",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_connect_returns_authorization_url(self, client, auth_headers):
        """Verify connect returns authorization URL in Location header."""
        mock_service = _create_mock_sso_service(
            get_authorization_url=("https://accounts.google.com/o/oauth2/v2/auth?state=connect_state", "connect_state")
        )
        with override_sso_service(mock_service):
            response = client.post(
                "/api/v1/account/sso/google/connect",
                headers=auth_headers,
                follow_redirects=False,
            )
            assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
            assert "state=connect_state" in response.headers["location"]


class TestSSODisconnectEndpoint:
    """Contract tests for DELETE /auth/sso/{provider} endpoint."""

    def test_disconnect_endpoint_exists(self, client, auth_headers):
        """Verify disconnect endpoint exists."""
        mock_service = _create_mock_sso_service(disconnect_provider={"message": "ok"})
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/google",
                headers=auth_headers,
            )
            assert response.status_code not in [404, 405]

    def test_disconnect_unknown_provider_returns_400(self, client, auth_headers):
        """Verify unknown provider returns 422 (Validation Error)."""
        response = client.delete(
            "/api/v1/account/sso/unknown_provider",
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_disconnect_returns_success_message(self, client, auth_headers):
        """Verify disconnect returns success message."""
        mock_service = _create_mock_sso_service(disconnect_provider={"message": "Provider disconnected successfully"})
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/google",
                headers=auth_headers,
            )
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "message" in data
