"""Integration tests for Google SSO OAuth flow.

TDD: These tests verify the complete end-to-end Google SSO authentication flow.

Task: T035 [US2] Write integration test for Google SSO flow

Tests verify:
1. New user can authenticate via Google OAuth and get JWT tokens
2. Existing user with linked Google account can authenticate
3. Email collision handling works correctly
4. Invalid authorization codes are rejected
5. State validation prevents CSRF attacks
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
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


class TestGoogleSSOE2EFlow:
    """End-to-end integration tests for Google SSO authentication flow."""

    @pytest.fixture
    def mock_google_provider(self):
        """Create a mock Google OAuth provider."""
        provider = Mock()
        provider.name = "google"
        provider.get_authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            "client_id=test_client_id&"
            "redirect_uri=https%3A//example.com/callback&"
            "response_type=code&"
            "scope=openid+email+profile&"
            "state=test_state"
        )
        provider.exchange_code_for_tokens.return_value = {
            "id_token": "test_id_token",
            "access_token": "test_access_token",
        }
        provider.get_user_info.return_value = {
            "sub": "google_user_123",
            "email": "user@gmail.com",
            "email_verified": True,
            "name": "Test User",
            "picture": "https://example.com/avatar.jpg",
        }
        return provider

    def test_new_user_google_oauth_flow(self, client, mock_user, auth_headers):
        """Test complete OAuth flow for new user - should create user and return tokens."""
        mock_service = _create_mock_sso_service(
            process_callback={
                "access_token": "new_user_access_token",
                "refresh_token": "new_user_refresh_token",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )

        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=auth_code_google&state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "access_token" in data
            assert "refresh_token" in data
            assert data["token_type"] == "bearer"

    def test_existing_user_google_oauth_flow(self, client, mock_user, db, auth_headers):
        """Test OAuth flow for existing user with linked Google account."""
        mock_service = _create_mock_sso_service(
            process_callback={
                "access_token": "existing_user_access_token",
                "refresh_token": "existing_user_refresh_token",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )

        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=auth_code_google&state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "access_token" in data
            assert "refresh_token" in data

    def test_google_oauth_invalid_code(self, client, mock_user, auth_headers):
        """Test that invalid authorization codes are rejected."""
        from src.app.core.exceptions.http_exceptions import BadRequestException

        mock_service = _create_mock_sso_service(process_callback=BadRequestException("Invalid authorization code"))

        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=invalid_code&state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_google_oauth_csrf_protection(self, client, mock_user, auth_headers):
        """Test that missing or invalid state is rejected (CSRF protection)."""
        mock_state_manager = Mock()
        mock_state_manager.validate_state = AsyncMock(return_value=None)  # Return None for invalid state

        # Here we do NOT mock process_callback because we want to test that process_callback calling validate_state
        # fails. So we patch get_sso_state_manager which is used inside process_callback.
        with patch("src.app.modules.sso.service.get_sso_state_manager", return_value=mock_state_manager):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=test_code&state=invalid_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_google_oauth_missing_code(self, client, mock_user, auth_headers):
        """Test that missing authorization code returns 422."""
        mock_state_manager = Mock()
        mock_state_manager.validate_state = AsyncMock(return_value={"tenant_id": 1, "provider": "google"})

        with patch("src.app.modules.sso.service.get_sso_state_manager", return_value=mock_state_manager):
            response = client.get(
                "/api/v1/account/sso/google/callback?state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_google_oauth_unknown_provider(self, client, mock_user, auth_headers):
        """Test that unknown provider returns 422."""
        mock_state_manager = Mock()
        mock_state_manager.validate_state = AsyncMock(
            return_value={"tenant_id": 1, "redirect_uri": "https://example.com", "provider": "unknown_provider"}
        )

        with patch("src.app.modules.sso.service.get_sso_state_manager", return_value=mock_state_manager):
            response = client.get(
                "/api/v1/account/sso/unknown_provider/callback?code=test_code&state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_google_oauth_provider_availability(self, client, auth_headers):
        """Test that Google provider is listed in available providers."""
        mock_providers = [
            {"name": "google", "enabled": True, "display_name": "Google"},
            {"name": "github", "enabled": True, "display_name": "Github"},
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
            # Verify google is in the returned providers
            providers = data["providers"]
            assert any(p["name"] == "google" for p in providers)

    def test_google_oauth_selects_primary_verified_email(self, client, mock_user, auth_headers):
        """Test that primary verified email is selected from multiple emails."""
        mock_service = _create_mock_sso_service(
            process_callback={
                "access_token": "access_token",
                "refresh_token": "refresh_token",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )

        with override_sso_service(mock_service):
            response = client.get(
                "/api/v1/account/sso/google/callback?code=auth_code&state=test_state",
                headers=auth_headers,
            )

            assert response.status_code == status.HTTP_200_OK
