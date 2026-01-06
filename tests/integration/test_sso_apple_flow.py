"""Integration tests for Apple SSO flow.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T056 [US5] Write integration test for Apple SSO flow

Apple Sign-In specifics:
- Uses POST callback (response_mode=form_post)
- Private email relay support
- JWT id_token for user info
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAppleSSOFlowIntegration:
    """Integration tests for Apple SSO authentication flow."""

    async def test_apple_authorize_redirects_to_apple(self, async_client: AsyncClient, mock_sso_settings):
        """Test that /auth/sso/apple/authorize redirects to Apple."""
        # Setup: Enable Apple SSO in settings
        mock_sso_settings("apple", enabled=True)

        response = await async_client.get("/api/v1/auth/sso/apple/authorize")

        assert response.status_code == 307  # Redirect
        location = response.headers.get("location")
        assert "appleid.apple.com/auth/authorize" in location
        assert "response_mode=form_post" in location

    async def test_apple_authorize_includes_required_params(self, async_client: AsyncClient, mock_sso_settings):
        """Test authorization URL includes all required parameters."""
        mock_sso_settings("apple", enabled=True)

        response = await async_client.get("/api/v1/auth/sso/apple/authorize")

        location = response.headers.get("location")
        assert "client_id=" in location
        assert "redirect_uri=" in location
        assert "scope=" in location
        assert "state=" in location
        assert "response_type=code" in location
        assert "response_mode=form_post" in location

    async def test_apple_callback_uses_post_method(
        self, async_client: AsyncClient, mock_sso_settings, mock_apple_oauth, mock_redis
    ):
        """Test Apple callback accepts POST method."""
        mock_sso_settings("apple", enabled=True)

        # Create mock state
        state = await self._create_valid_state(mock_redis, "apple")

        # Create mock id_token
        id_token = self._create_mock_id_token()

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
                "token_type": "bearer",
            }
        )

        # Apple uses POST for callback
        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code", "state": state},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code in [200, 302]  # Success or redirect with token

    async def test_apple_callback_creates_new_user(
        self, async_client: AsyncClient, db_session, mock_sso_settings, mock_apple_oauth, mock_redis
    ):
        """Test Apple callback creates new user on first login."""
        mock_sso_settings("apple", enabled=True)

        state = await self._create_valid_state(mock_redis, "apple")
        id_token = self._create_mock_id_token(
            sub="001234.unique.id", email="newuser@example.com"
        )

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
            }
        )

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={
                "code": "test-code",
                "state": state,
                "user": '{"name":{"firstName":"John","lastName":"Doe"}}',
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

        # Verify user was created in database
        from src.app.modules.users.crud import crud_users

        user = await crud_users.get(db_session, email="newuser@example.com")
        assert user is not None

    async def test_apple_callback_links_existing_user_by_email(
        self, async_client: AsyncClient, db_session, mock_sso_settings, mock_apple_oauth, create_test_user, mock_redis
    ):
        """Test Apple callback links to existing user if email matches."""
        # Create existing user
        existing_user = await create_test_user(email="existing@example.com")

        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")
        id_token = self._create_mock_id_token(
            sub="001234.apple.id", email="existing@example.com"
        )

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
            }
        )

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code", "state": state},
        )

        assert response.status_code == 200

        # Verify SSO connection was created
        from src.app.modules.sso.crud import crud_user_sso_connections

        connection = await crud_user_sso_connections.get(
            db_session, provider="apple", provider_user_id="001234.apple.id"
        )
        assert connection is not None
        assert connection.user_id == existing_user.id

    async def test_apple_callback_handles_private_email_relay(
        self, async_client: AsyncClient, db_session, mock_sso_settings, mock_apple_oauth, mock_redis
    ):
        """Test Apple callback handles private email relay correctly."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")

        # Private email relay email
        id_token = self._create_mock_id_token(
            sub="001234.private.id",
            email="abc123@privaterelay.appleid.com",
            is_private_email=True,
        )

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
            }
        )

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code", "state": state},
        )

        assert response.status_code == 200

        # Verify user was created with private email
        from src.app.modules.users.crud import crud_users

        user = await crud_users.get(db_session, email="abc123@privaterelay.appleid.com")
        assert user is not None

    async def test_apple_callback_extracts_name_on_first_login(
        self, async_client: AsyncClient, db_session, mock_sso_settings, mock_apple_oauth, mock_redis
    ):
        """Test Apple callback extracts name from POST body on first login."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")
        id_token = self._create_mock_id_token(
            sub="001234.name.id", email="nameduser@example.com"
        )

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
            }
        )

        # Apple sends user name in POST body only on first authorization
        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={
                "code": "test-code",
                "state": state,
                "user": '{"name":{"firstName":"Jane","lastName":"Smith"}}',
            },
        )

        assert response.status_code == 200

        from src.app.modules.users.crud import crud_users

        user = await crud_users.get(db_session, email="nameduser@example.com")
        assert user is not None
        assert user["name"] == "Jane Smith"

    async def test_apple_callback_fails_without_state(
        self, async_client: AsyncClient, mock_sso_settings, mock_apple_oauth
    ):
        """Test Apple callback rejects request without state parameter."""
        mock_sso_settings("apple", enabled=True)

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code"},  # No state
        )

        assert response.status_code == 422
        # FastAPI returns a list of validation errors
        errors = response.json().get("detail", [])
        assert any("state" in str(e).lower() for e in errors)

    async def test_apple_callback_fails_with_invalid_state(
        self, async_client: AsyncClient, mock_sso_settings, mock_apple_oauth
    ):
        """Test Apple callback rejects invalid state parameter."""
        mock_sso_settings("apple", enabled=True)

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code", "state": "invalid-state"},
        )

        assert response.status_code == 400

    async def test_apple_provider_disabled(self, async_client: AsyncClient, mock_sso_settings):
        """Test Apple SSO fails when provider is disabled."""
        mock_sso_settings("apple", enabled=False)

        response = await async_client.get("/api/v1/auth/sso/apple/authorize")

        assert response.status_code == 400
        assert "disabled" in response.json().get("detail", "").lower()

    async def test_apple_subsequent_login_returns_tokens(
        self, async_client: AsyncClient, db_session, mock_sso_settings, mock_apple_oauth, create_test_user, mock_redis
    ):
        """Test Apple login returns tokens for existing Apple user."""
        # Create user with Apple connection
        existing_user = await create_test_user(email="appleuser@example.com")

        from src.app.modules.sso.crud import crud_user_sso_connections
        from src.app.modules.sso.schemas import UserSSOConnectionCreate

        await crud_user_sso_connections.create(
            db_session,
            object=UserSSOConnectionCreate(
                user_id=existing_user.id,
                tenant_id=existing_user.tenant_id,
                provider="apple",
                provider_user_id="existing.apple.id",
                provider_email="appleuser@example.com",
            ),
        )
        await db_session.commit()

        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")
        id_token = self._create_mock_id_token(
            sub="existing.apple.id", email="appleuser@example.com"
        )

        mock_apple_oauth(
            token_response={
                "access_token": "test-access-token",
                "id_token": id_token,
            }
        )

        response = await async_client.post(
            "/api/v1/auth/sso/apple/callback",
            data={"code": "test-code", "state": state},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    # Helper methods

    async def _create_valid_state(self, mock_redis, provider: str) -> str:
        """Create a valid SSO state token for testing."""
        from src.app.core.security.tokens import get_sso_state_manager

        # mocked redis passed directly
        sso_manager = get_sso_state_manager(mock_redis)

        state = await sso_manager.create_state(
            provider=provider,
            redirect_uri=f"http://testserver/api/v1/auth/sso/{provider}/callback",
        )
        return state

    def _create_mock_id_token(
        self,
        sub: str = "test.apple.id",
        email: str = "test@example.com",
        email_verified: bool = True,
        is_private_email: bool = False,
    ) -> str:
        """Create a mock Apple id_token JWT."""
        payload = {
            "iss": "https://appleid.apple.com",
            "aud": "com.example.app",
            "sub": sub,
            "email": email,
            "email_verified": email_verified,
            "is_private_email": is_private_email,
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iat": int(datetime.now(UTC).timestamp()),
        }
        return jwt.encode(payload, "secret", algorithm="HS256")


# Fixtures for Apple SSO testing

@pytest.fixture
def mock_sso_settings():
    """Fixture to mock SSO settings for Apple provider."""

    patches = []

    def _mock_sso_settings(provider: str, enabled: bool = True):
        patcher = patch(
            "src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings",
            new_callable=AsyncMock,
        )
        mock = patcher.start()
        patches.append(patcher)

        mock.return_value = {
            "client_id": "com.example.app",
            "client_secret": "test-secret",
            "team_id": "TEAMID1234",
            "key_id": "KEYID12345",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
            "is_enabled": enabled,
        }
        return mock

    yield _mock_sso_settings

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def mock_apple_oauth():
    """Fixture to mock Apple OAuth responses."""

    patches = []

    def _mock_apple_oauth(token_response: dict = None):
        patcher = patch(
            "src.app.modules.sso.providers.apple.AppleProvider.exchange_code_for_token",
            new_callable=AsyncMock,
        )
        mock_token = patcher.start()
        patches.append(patcher)

        mock_token.return_value = token_response or {
            "access_token": "mock-access-token",
            "id_token": "mock-id-token",
        }
        return mock_token

    yield _mock_apple_oauth

    for patcher in patches:
        patcher.stop()
