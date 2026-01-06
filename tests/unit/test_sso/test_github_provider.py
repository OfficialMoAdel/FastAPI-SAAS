"""Unit tests for GitHubProvider.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T039 [US3] Write unit tests for GitHubProvider
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestGitHubProvider:
    """Test GitHubProvider OAuth2 implementation."""

    def test_github_provider_exists(self):
        """Verify GitHubProvider can be imported."""
        from src.app.modules.sso.providers.github import GitHubProvider

        assert GitHubProvider is not None

    def test_github_provider_inherits_base(self):
        """Verify GitHubProvider extends BaseOAuthProvider."""
        from src.app.modules.sso.providers.base import BaseOAuthProvider
        from src.app.modules.sso.providers.github import GitHubProvider

        assert issubclass(GitHubProvider, BaseOAuthProvider)

    def test_github_provider_provider_name(self):
        """Verify provider_name is 'github'."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.provider_name == "github"

    def test_github_provider_endpoints(self):
        """Verify OAuth endpoints are correct for GitHub."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.authorization_endpoint == "https://github.com/login/oauth/authorize"
        assert provider.token_endpoint == "https://github.com/login/oauth/access_token"
        assert provider.userinfo_endpoint == "https://api.github.com/user"

    def test_github_provider_default_scopes(self):
        """Verify default scopes include read:user and user:email."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        scopes = provider.get_default_scopes()
        assert "read:user" in scopes
        assert "user:email" in scopes

    def test_github_provider_not_oidc_compliant(self):
        """Verify GitHub is not OIDC compliant."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.is_oidc_compliant is False

    def test_github_provider_requires_verified_email(self):
        """Verify GitHub requires verified email."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.requires_verified_email() is True

    @pytest.mark.asyncio
    async def test_github_exchange_code_for_token(self):
        """Verify code exchange with GitHub returns access token."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_response = {
            "access_token": "test-access-token",
            "token_type": "bearer",
            "scope": "read:user,user:email",
        }

        with patch.object(provider, "_http_post", new_callable=AsyncMock, return_value=mock_response):
            result = await provider.exchange_code_for_token("test-code")

        assert result["access_token"] == "test-access-token"
        assert result["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_github_get_user_info(self):
        """Verify user info retrieval from GitHub."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": None,  # GitHub may not return email in user endpoint
            "avatar_url": "https://avatars.githubusercontent.com/u/12345?v=4",
        }

        mock_emails_data = [
            {
                "email": "test@example.com",
                "primary": True,
                "verified": True,
            }
        ]

        # First call for user info, second for emails
        with patch.object(
            provider, "_http_get", new_callable=AsyncMock, side_effect=[mock_user_data, mock_emails_data]
        ):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.provider_user_id == "12345"
        assert user_info.email == "test@example.com"
        assert user_info.email_verified is True
        assert user_info.name == "Test User"
        assert user_info.picture == "https://avatars.githubusercontent.com/u/12345?v=4"

    @pytest.mark.asyncio
    async def test_github_get_user_info_no_email_scope(self):
        """Verify user info when email scope is not granted."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": None,
            "avatar_url": "https://avatars.githubusercontent.com/u/12345?v=4",
        }

        # Email endpoint fails (no scope)
        with patch.object(
            provider, "_http_get", new_callable=AsyncMock, side_effect=[mock_user_data, Exception("Not Found")]
        ):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.provider_user_id == "12345"
        assert user_info.email is None
        assert user_info.email_verified is False
        assert user_info.name == "Test User"

    @pytest.mark.asyncio
    async def test_github_get_user_info_uses_login_as_name_fallback(self):
        """Verify login is used as name when name is None."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": 12345,
            "login": "testuser",
            "name": None,  # No name
            "email": None,
            "avatar_url": "https://avatars.githubusercontent.com/u/12345?v=4",
        }

        with patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.name == "testuser"

    @pytest.mark.asyncio
    async def test_github_get_user_info_selects_primary_verified_email(self):
        """Verify primary verified email is selected from multiple emails."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": None,
        }

        mock_emails_data = [
            {"email": "secondary@example.com", "primary": False, "verified": True},
            {"email": "test@example.com", "primary": True, "verified": True},
            {"email": "unverified@example.com", "primary": False, "verified": False},
        ]

        with patch.object(
            provider, "_http_get", new_callable=AsyncMock, side_effect=[mock_user_data, mock_emails_data]
        ):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.email == "test@example.com"
        assert user_info.email_verified is True

    def test_github_get_authorization_url(self):
        """Verify authorization URL generation for GitHub."""
        from src.app.modules.sso.providers.github import GitHubProvider

        provider = GitHubProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        url = provider.get_authorization_url(state="test-state")

        assert "github.com/login/oauth/authorize" in url
        assert "client_id=test-client-id" in url
        # redirect_uri is URL-encoded
        assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcallback" in url
        assert "state=test-state" in url
