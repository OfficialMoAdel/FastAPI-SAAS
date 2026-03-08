"""Unit tests for AppleProvider.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T055 [US5] Write unit tests for AppleProvider

Apple Sign-In specifics:
- Uses POST callback (not GET like other providers)
- Private email relay support (privaterelay.appleid.com)
- Scopes: name email
- Uses JWT id_token for user info (no separate userinfo endpoint)
- Client secret is a signed JWT (not static string)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest


class TestAppleProvider:
    """Test AppleProvider OAuth2 implementation."""

    def test_apple_provider_exists(self):
        """Verify AppleProvider can be imported."""
        from src.app.modules.sso.providers.apple import AppleProvider

        assert AppleProvider is not None

    def test_apple_provider_inherits_base(self):
        """Verify AppleProvider extends BaseOAuthProvider."""
        from src.app.modules.sso.providers.apple import AppleProvider
        from src.app.modules.sso.providers.base import BaseOAuthProvider

        assert issubclass(AppleProvider, BaseOAuthProvider)

    def test_apple_provider_provider_name(self):
        """Verify provider_name is 'apple'."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",  # Apple uses special format
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        assert provider.provider_name == "apple"

    def test_apple_provider_endpoints(self):
        """Verify OAuth endpoints are correct for Apple."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        assert provider.authorization_endpoint == "https://appleid.apple.com/auth/authorize"
        assert provider.token_endpoint == "https://appleid.apple.com/auth/token"
        # Apple doesn't have a userinfo endpoint - uses id_token JWT
        assert provider.userinfo_endpoint is None

    def test_apple_provider_default_scopes(self):
        """Verify default scopes include name and email."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        scopes = provider.get_default_scopes()
        assert "name" in scopes
        assert "email" in scopes

    def test_apple_provider_is_oidc_compliant(self):
        """Verify Apple is OIDC compliant."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        assert provider.is_oidc_compliant is True

    def test_apple_provider_uses_post_callback(self):
        """Verify Apple uses POST for callback."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        assert provider.uses_post_callback is True

    def test_apple_provider_supports_private_email_relay(self):
        """Verify Apple supports private email relay."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        assert provider.supports_private_email_relay is True

    def test_apple_provider_requires_additional_params(self):
        """Verify Apple requires team_id, key_id, and private_key."""
        from src.app.modules.sso.providers.apple import AppleProvider

        # Should raise without required params
        with pytest.raises(TypeError):
            AppleProvider(
                client_id="com.example.app",
                client_secret="test-secret",
                redirect_uri="https://example.com/callback",
            )

    @pytest.mark.asyncio
    async def test_apple_exchange_code_for_token(self):
        """Verify code exchange with Apple returns access token and id_token."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        # Create a mock id_token JWT
        id_token_payload = {
            "sub": "001234.abcdef.1234",
            "email": "user@privaterelay.appleid.com",
            "email_verified": True,
            "is_private_email": True,
            "iss": "https://appleid.apple.com",
            "aud": "com.example.app",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        mock_id_token = jwt.encode(id_token_payload, "secret", algorithm="HS256")

        mock_response = {
            "access_token": "test-access-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "test-refresh-token",
            "id_token": mock_id_token,
        }

        with patch.object(provider, "_http_post", new_callable=AsyncMock, return_value=mock_response):
            with patch.object(provider, "_generate_client_secret", return_value="generated-secret"):
                result = await provider.exchange_code_for_token("test-code")

        assert result["access_token"] == "test-access-token"
        assert result["id_token"] == mock_id_token
        assert result["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_apple_get_user_info_from_id_token(self):
        """Verify user info extraction from Apple id_token JWT."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        # Create a mock id_token JWT
        id_token_payload = {
            "sub": "001234.abcdef.1234",
            "email": "user@example.com",
            "email_verified": True,
            "is_private_email": False,
            "iss": "https://appleid.apple.com",
            "aud": "com.example.app",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        mock_id_token = jwt.encode(id_token_payload, "secret", algorithm="HS256")

        with patch.object(provider, "_verify_id_token", return_value=id_token_payload):
            user_info = await provider.get_user_info(mock_id_token)

        assert user_info.provider_user_id == "001234.abcdef.1234"
        assert user_info.email == "user@example.com"
        assert user_info.email_verified is True
        assert user_info.is_private_email is False

    @pytest.mark.asyncio
    async def test_apple_get_user_info_private_email_relay(self):
        """Verify user info handling for private email relay."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        id_token_payload = {
            "sub": "001234.abcdef.1234",
            "email": "abc123@privaterelay.appleid.com",
            "email_verified": True,
            "is_private_email": True,
            "iss": "https://appleid.apple.com",
            "aud": "com.example.app",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        mock_id_token = jwt.encode(id_token_payload, "secret", algorithm="HS256")

        with patch.object(provider, "_verify_id_token", return_value=id_token_payload):
            user_info = await provider.get_user_info(mock_id_token)

        assert user_info.provider_user_id == "001234.abcdef.1234"
        assert user_info.email == "abc123@privaterelay.appleid.com"
        assert user_info.is_private_email is True

    @pytest.mark.asyncio
    async def test_apple_get_user_info_with_name(self):
        """Verify user info extraction when name is provided (first login only)."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        id_token_payload = {
            "sub": "001234.abcdef.1234",
            "email": "user@example.com",
            "email_verified": True,
            "iss": "https://appleid.apple.com",
            "aud": "com.example.app",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        mock_id_token = jwt.encode(id_token_payload, "secret", algorithm="HS256")

        # Apple sends name in POST body on first auth only
        user_data = {"name": {"firstName": "John", "lastName": "Doe"}}

        with patch.object(provider, "_verify_id_token", return_value=id_token_payload):
            user_info = await provider.get_user_info(mock_id_token, user_data=user_data)

        assert user_info.provider_user_id == "001234.abcdef.1234"
        assert user_info.name == "John Doe"

    def test_apple_get_authorization_url(self):
        """Verify authorization URL generation for Apple."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        url = provider.get_authorization_url(state="test-state")

        assert "appleid.apple.com/auth/authorize" in url
        assert "client_id=com.example.app" in url
        assert "response_type=code" in url
        assert "response_mode=form_post" in url  # Apple uses form_post
        assert "state=test-state" in url

    def test_apple_authorization_url_includes_response_mode_form_post(self):
        """Verify authorization URL includes response_mode=form_post for POST callback."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )

        url = provider.get_authorization_url(state="test-state")

        assert "response_mode=form_post" in url

    @pytest.mark.asyncio
    async def test_apple_generate_client_secret(self):
        """Verify client secret JWT generation."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from src.app.modules.sso.providers.apple import AppleProvider

        # Generate a real EC private key for testing
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="placeholder",  # Not used, we generate JWT
            redirect_uri="https://example.com/callback",
            team_id="TEAMID1234",
            key_id="KEYID12345",
            private_key=private_key_pem,
        )

        secret = provider._generate_client_secret()

        # Verify it's a valid JWT
        assert secret is not None
        # Decode without verification to check structure
        decoded = jwt.decode(secret, options={"verify_signature": False})
        assert decoded["iss"] == "TEAMID1234"
        assert decoded["sub"] == "com.example.app"
        assert decoded["aud"] == "https://appleid.apple.com"
        assert "exp" in decoded
        assert "iat" in decoded

    @pytest.mark.asyncio
    async def test_apple_requires_verified_email(self):
        """Verify Apple returns email_verified status."""
        from src.app.modules.sso.providers.apple import AppleProvider

        provider = AppleProvider(
            client_id="com.example.app",
            client_secret="test-team-id:test-key-id",
            redirect_uri="https://example.com/callback",
            team_id="test-team-id",
            key_id="test-key-id",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        # Apple always returns verified emails
        assert provider.requires_verified_email() is True
