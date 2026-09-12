"""Unit tests for MicrosoftProvider.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T048 [US4] Write unit tests for MicrosoftProvider
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestMicrosoftProvider:
    """Test MicrosoftProvider OAuth2 implementation."""

    def test_microsoft_provider_exists(self):
        """Verify MicrosoftProvider can be imported."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        assert MicrosoftProvider is not None

    def test_microsoft_provider_inherits_base(self):
        """Verify MicrosoftProvider extends BaseOAuthProvider."""
        from src.app.modules.auth.providers.base import BaseOAuthProvider
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        assert issubclass(MicrosoftProvider, BaseOAuthProvider)

    def test_microsoft_provider_provider_name(self):
        """Verify provider_name is 'microsoft'."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.provider_name == "microsoft"

    def test_microsoft_provider_endpoints(self):
        """Verify OAuth endpoints are correct for Microsoft."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.authorization_endpoint == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert provider.token_endpoint == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        assert provider.userinfo_endpoint == "https://graph.microsoft.com/v1.0/me"

    def test_microsoft_provider_default_scopes(self):
        """Verify default scopes include openid, email, and profile."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        scopes = provider.get_default_scopes()
        assert "openid" in scopes
        assert "email" in scopes
        assert "profile" in scopes

    def test_microsoft_provider_is_oidc_compliant(self):
        """Verify Microsoft is OIDC compliant."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.is_oidc_compliant is True

    def test_microsoft_provider_requires_verified_email(self):
        """Verify Microsoft requires verified email."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        assert provider.requires_verified_email() is True

    @pytest.mark.asyncio
    async def test_microsoft_exchange_code_for_token(self):
        """Verify code exchange with Microsoft returns access token."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_response = {
            "access_token": "test-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid email profile",
        }

        with patch.object(provider, "_http_post", new_callable=AsyncMock, return_value=mock_response):
            result = await provider.exchange_code_for_token("test-code")

        assert result["access_token"] == "test-access-token"
        assert result["token_type"] == "Bearer"

    @pytest.mark.asyncio
    async def test_microsoft_get_user_info(self):
        """Verify user info retrieval from Microsoft Graph API."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "displayName": "Test User",
            "mail": "test@example.com",
            "userPrincipalName": "test@example.com",
            "givenName": "Test",
        }

        with patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.provider_user_id == "12345678-1234-1234-1234-123456789012"
        assert user_info.email == "test@example.com"
        # M10: no id_token -> email_verified defaults to False (fail closed)
        assert user_info.email_verified is False
        assert user_info.name == "Test User"
        assert user_info.picture is None

    @pytest.mark.asyncio
    async def test_microsoft_get_user_info_email_verified_from_id_token(self):
        """M10: email_verified follows the id_token `email_verified` claim.

        P2-1: id_token signature is verified via JWKS (_verify_id_token).
        Tests mock the verification seam instead of crafting unsigned tokens.
        """
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        mock_user_data = {"id": "abc", "mail": "test@example.com"}

        async def _claims(claims: dict) -> dict:
            return claims

        with (
            patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data),
            patch.object(
                provider,
                "_verify_id_token",
                new_callable=AsyncMock,
                side_effect=[
                    {"email_verified": True},
                    {"email_verified": False},
                    {"email": "x@y.z"},
                ],
            ),
        ):
            verified = await provider.get_user_info("tok", id_token="signed.jwt.here")
            explicit_false = await provider.get_user_info("tok", id_token="signed.jwt.here")
            omitted = await provider.get_user_info("tok", id_token="signed.jwt.here")

        assert verified.email_verified is True   # M10: explicit true only
        assert explicit_false.email_verified is False  # M10: explicit false fails closed
        assert omitted.email_verified is False  # M10: omitted claim defaults False

    @pytest.mark.asyncio
    async def test_microsoft_get_user_info_unverified_signature_fails_closed(self):
        """P2-1: forged id_token (verification failure) -> email_verified False."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )
        mock_user_data = {"id": "abc", "mail": "test@example.com"}

        with (
            patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data),
            patch.object(
                provider,
                "_verify_id_token",
                new_callable=AsyncMock,
                side_effect=ValueError("Invalid id_token: Signature verification failed"),
            ),
        ):
            user_info = await provider.get_user_info("tok", id_token="forged.jwt.here")

        assert user_info.email_verified is False

    @pytest.mark.asyncio
    async def test_microsoft_get_user_info_uses_userprincipalname_fallback(self):
        """Verify userPrincipalName is used when mail is None."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "displayName": "Test User",
            "mail": None,  # No mail field
            "userPrincipalName": "test@contoso.onmicrosoft.com",
            "givenName": "Test",
            "emailVerified": True,
        }

        with patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.email == "test@contoso.onmicrosoft.com"

    @pytest.mark.asyncio
    async def test_microsoft_get_user_info_uses_displayname(self):
        """Verify displayName is preferred over givenName."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        mock_user_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "displayName": "Full Name",
            "mail": "test@example.com",
            "givenName": "First",
        }

        with patch.object(provider, "_http_get", new_callable=AsyncMock, return_value=mock_user_data):
            user_info = await provider.get_user_info("test-access-token")

        assert user_info.name == "Full Name"

    def test_microsoft_get_authorization_url(self):
        """Verify authorization URL generation for Microsoft."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        provider = MicrosoftProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost/callback",
        )

        url = provider.get_authorization_url(state="test-state")

        assert "login.microsoftonline.com/common/oauth2/v2.0/authorize" in url
        assert "client_id=test-client-id" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcallback" in url
        assert "state=test-state" in url
