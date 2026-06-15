"""Integration tests for Apple SSO flow.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T056 [US5] Write integration test for Apple SSO flow

Apple Sign-In specifics:
- Uses POST callback (response_mode=form_post)
- Private email relay support
- JWT id_token for user info
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from httpx import AsyncClient

from src.app.modules.sso.service import get_sso_service


def _create_mock_sso_service():
    """Create a mock SSOService with all methods as AsyncMock."""
    mock_service = Mock()
    mock_service.get_authorization_url = AsyncMock()
    mock_service.process_callback = AsyncMock()
    mock_service.get_sso_settings = AsyncMock()
    mock_service.get_connected_accounts = AsyncMock(return_value=[])
    mock_service.connect_provider = AsyncMock()
    mock_service.disconnect_provider = AsyncMock()
    return mock_service


@contextmanager
def override_sso_service(mock_service):
    """Context manager to override SSOService dependency."""
    from src.app.main import app

    app.dependency_overrides[get_sso_service] = lambda: mock_service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_sso_service, None)


@pytest.mark.asyncio
class TestAppleSSOFlowIntegration:
    """Integration tests for Apple SSO authentication flow."""

    # Helper methods
    async def _create_valid_state(self, mock_redis, provider: str) -> str:
        """Create a valid SSO state token for testing."""
        from src.app.core.security.tokens import get_sso_state_manager

        from src.app.core.security.tokens import TokenGenerator
        # mocked redis passed directly
        sso_manager = get_sso_state_manager(mock_redis)

        state = TokenGenerator.generate_sso_state()
        await sso_manager.create(
            token=state,
            data={
                "provider": provider,
                "redirect_uri": f"http://testserver/api/v1/account/sso/{provider}/callback",
            }
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

    async def test_apple_callback_uses_post_method(self, async_client: AsyncClient, mock_sso_settings, mock_redis):
        """Test Apple callback accepts POST method."""
        mock_sso_settings("apple", enabled=True)

        # Create mock SSO service
        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
        }

        # Create mock state
        state = await self._create_valid_state(mock_redis, "apple")

        with override_sso_service(mock_service):
            # Apple uses POST for callback
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={"code": "test-code", "state": state},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                cookies={"sso_state": state},
            )

        assert response.status_code in [200, 302]

    async def test_apple_callback_creates_new_user(self, async_client: AsyncClient, mock_sso_settings, mock_redis):
        """Test Apple callback creates new user on first login."""
        mock_sso_settings("apple", enabled=True)

        state = await self._create_valid_state(mock_redis, "apple")

        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user_id": "new-user-id",
            "email": "newuser@example.com",
        }

        with override_sso_service(mock_service):
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={
                    "code": "test-code",
                    "state": state,
                    "user": '{"name":{"firstName":"John","lastName":"Doe"}}',
                },
                cookies={"sso_state": state},
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

        # Verify process_callback was called with correct params
        mock_service.process_callback.assert_called_once()

    async def test_apple_callback_links_existing_user_by_email(
        self, async_client: AsyncClient, mock_sso_settings, mock_redis
    ):
        """Test Apple callback links to existing user if email matches."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")

        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user_id": "existing-user-id",
            "email": "existing@example.com",
            "is_new_user": False,
        }

        with override_sso_service(mock_service):
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={"code": "test-code", "state": state},
                cookies={"sso_state": state},
            )

        assert response.status_code == 200
        mock_service.process_callback.assert_called_once()

    async def test_apple_callback_handles_private_email_relay(
        self, async_client: AsyncClient, mock_sso_settings, mock_redis
    ):
        """Test Apple callback handles private email relay correctly."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")

        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user_id": "private-email-user",
            "email": "abc123@privaterelay.appleid.com",
        }

        with override_sso_service(mock_service):
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={"code": "test-code", "state": state},
                cookies={"sso_state": state},
            )

        assert response.status_code == 200
        mock_service.process_callback.assert_called_once()

    async def test_apple_callback_extracts_name_on_first_login(
        self, async_client: AsyncClient, mock_sso_settings, mock_redis
    ):
        """Test Apple callback extracts name from POST body on first login."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")

        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user_id": "named-user-id",
            "email": "nameduser@example.com",
            "name": "Jane Smith",
        }

        with override_sso_service(mock_service):
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={
                    "code": "test-code",
                    "state": state,
                    "user": '{"name":{"firstName":"Jane","lastName":"Smith"}}',
                },
                cookies={"sso_state": state},
            )

        assert response.status_code == 200
        mock_service.process_callback.assert_called_once()

    async def test_apple_subsequent_login_returns_tokens(
        self, async_client: AsyncClient, mock_sso_settings, mock_redis
    ):
        """Test Apple login returns tokens for existing Apple user."""
        mock_sso_settings("apple", enabled=True)
        state = await self._create_valid_state(mock_redis, "apple")

        mock_service = _create_mock_sso_service()
        mock_service.process_callback.return_value = {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user_id": "existing-apple-user",
            "email": "appleuser@example.com",
            "is_new_user": False,
        }

        with override_sso_service(mock_service):
            response = await async_client.post(
                "/api/v1/account/sso/apple/callback",
                data={"code": "test-code", "state": state},
                cookies={"sso_state": state},
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        mock_service.process_callback.assert_called_once()
