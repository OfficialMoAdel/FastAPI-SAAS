"""Unit tests for GoogleProvider.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T033 [US2] Write unit tests for GoogleProvider
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestBaseOAuthProvider:
    """Test BaseOAuthProvider abstract base class."""

    def test_base_provider_is_abstract(self):
        """Verify BaseOAuthProvider cannot be instantiated directly."""
        from src.app.modules.auth.providers.base import BaseOAuthProvider

        with pytest.raises(TypeError):
            BaseOAuthProvider()

    def test_base_provider_has_required_abstract_methods(self):
        """Verify abstract methods are defined."""

        from src.app.modules.auth.providers.base import BaseOAuthProvider

        # Check abstract methods
        abstract_methods = set()
        for name in dir(BaseOAuthProvider):
            method = getattr(BaseOAuthProvider, name, None)
            if getattr(method, "__isabstractmethod__", False):
                abstract_methods.add(name)

        assert "exchange_code_for_token" in abstract_methods
        assert "get_user_info" in abstract_methods


class TestGoogleProvider:
    """Test GoogleProvider OAuth2 implementation."""

    def test_google_provider_exists(self):
        """Verify GoogleProvider can be imported."""
        from src.app.modules.auth.providers.google import GoogleProvider

        assert GoogleProvider is not None

    def test_google_provider_inherits_base(self):
        """Verify GoogleProvider extends BaseOAuthProvider."""
        from src.app.modules.auth.providers.base import BaseOAuthProvider
        from src.app.modules.auth.providers.google import GoogleProvider

        assert issubclass(GoogleProvider, BaseOAuthProvider)

    def test_google_provider_provider_name(self):
        """Verify provider_name is 'google'."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert provider.provider_name == "google"

    def test_google_provider_authorization_endpoint(self):
        """Verify correct Google authorization endpoint."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert "accounts.google.com" in provider.authorization_endpoint

    def test_google_provider_token_endpoint(self):
        """Verify correct Google token endpoint."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert "oauth2.googleapis.com/token" in provider.token_endpoint

    def test_google_provider_userinfo_endpoint(self):
        """Verify correct Google userinfo endpoint."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert "googleapis.com/oauth2/v3/userinfo" in provider.userinfo_endpoint

    def test_google_provider_default_scopes(self):
        """Verify default scopes include email and profile."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert "openid" in provider.scopes
        assert "email" in provider.scopes
        assert "profile" in provider.scopes

    def test_google_provider_get_authorization_url_returns_url(self):
        """Verify get_authorization_url returns proper OAuth URL."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )

        url = provider.get_authorization_url(state="test-state-123")

        assert "accounts.google.com" in url
        assert "client_id=test-client-id" in url
        assert "state=test-state-123" in url
        assert "redirect_uri=" in url

    def test_google_provider_authorization_url_includes_scopes(self):
        """Verify authorization URL includes required scopes."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )

        url = provider.get_authorization_url(state="test-state")

        assert "scope=" in url
        assert "email" in url
        assert "openid" in url

    @pytest.mark.asyncio
    async def test_google_provider_exchange_code_returns_tokens(self):
        """Verify exchange_code_for_token returns access token dict."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )

        mock_response = {
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": "mock-id-token",
        }

        with patch.object(provider, "_http_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await provider.exchange_code_for_token(code="auth-code-123")

            assert result["access_token"] == "mock-access-token"
            assert "token_type" in result

    @pytest.mark.asyncio
    async def test_google_provider_get_user_info_returns_user_data(self):
        """Verify get_user_info returns structured user data."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )

        mock_response = {
            "sub": "google-user-id-123",
            "email": "user@gmail.com",
            "email_verified": True,
            "name": "Test User",
            "picture": "https://example.com/photo.jpg",
        }

        with patch.object(provider, "_http_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await provider.get_user_info(access_token="mock-token")

            assert result.provider_user_id == "google-user-id-123"
            assert result.email == "user@gmail.com"
            assert result.email_verified is True
            assert result.name == "Test User"

    def test_google_provider_is_oidc_compliant(self):
        """Verify GoogleProvider is marked as OIDC compliant."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )
        assert provider.is_oidc_compliant is True

    def test_google_provider_validates_email_verified(self):
        """Verify provider can check if email is verified."""
        from src.app.modules.auth.providers.google import GoogleProvider

        provider = GoogleProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="https://example.com/callback",
        )

        # Provider should have method to check email verification
        assert hasattr(provider, "requires_verified_email")
