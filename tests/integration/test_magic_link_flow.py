"""Integration test for complete Magic Link authentication flow.

TDD: This test is written first and should FAIL until implementation is complete.

Task: T022 [US1] Write integration test for magic link flow

This test validates the complete end-to-end flow:
1. User requests magic link via email
2. System creates token and queues email
3. User clicks link and submits token
4. System validates token and returns JWT tokens
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestMagicLinkE2EFlow:
    """End-to-end integration tests for magic link authentication."""

    @pytest.fixture
    def mock_email_service(self):
        """Mock email sending to prevent actual email delivery."""
        return AsyncMock()

    @pytest.fixture
    def test_user_data(self):
        """Test user data for the flow."""
        return {
            "email": "magic-link-test@example.com",
            "id": 1,
            "is_active": True,
        }

    @pytest.mark.asyncio
    async def test_complete_magic_link_flow(
        self,
        client,
        test_user_data,
        mock_email_service,
        auth_headers,
    ):
        """Test complete magic link flow: send -> verify -> get tokens."""
        email = test_user_data["email"]

        # Step 1: Request magic link
        with (
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._get_user_by_email",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._queue_magic_link_email",
                new_callable=AsyncMock,
            ),
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._get_magic_link_manager",
            ) as mock_manager,
        ):
            # Setup mock user as dict (service uses dict-style access)
            mock_get_user.return_value = test_user_data

            # Setup mock manager to return token
            mock_token = "test_magic_link_token_12345678901234567890"
            mock_magic_link_manager = AsyncMock()
            mock_magic_link_manager.create_token.return_value = mock_token
            mock_manager.return_value = mock_magic_link_manager

            # Send magic link request
            response = client.post(
                "/api/v1/auth/magic-link/send",
                json={"email": email},
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "message" in data

        # Step 2: Verify the magic link token
        with (
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._get_user_by_email",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._get_magic_link_manager",
            ) as mock_manager,
            patch(
                "src.app.modules.magic_link.service.MagicLinkService._create_auth_tokens",
                new_callable=AsyncMock,
            ) as mock_create_tokens,
        ):
            # Setup mocks - use dict with is_deleted added (service uses dict-style access)
            user_data_with_delete = {**test_user_data, "is_deleted": False}
            mock_get_user.return_value = user_data_with_delete

            mock_magic_link_manager = AsyncMock()
            mock_magic_link_manager.validate_token.return_value = {
                "email": email,
                "action": "login",
            }
            mock_manager.return_value = mock_magic_link_manager

            mock_create_tokens.return_value = {
                "access_token": "jwt_access_token_123",
                "refresh_token": "jwt_refresh_token_456",
                "token_type": "bearer",
                "expires_in": 3600,
            }

            # Verify magic link
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": mock_token},
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert "refresh_token" in data
            assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_magic_link_flow_nonexistent_user(self, client, auth_headers):
        """Test flow with nonexistent user (should not reveal user existence)."""
        with patch(
            "src.app.modules.magic_link.service.MagicLinkService._get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,  # User doesn't exist
        ):
            response = client.post(
                "/api/v1/auth/magic-link/send",
                json={"email": "nonexistent@example.com"},
                headers=auth_headers,
            )

            # Should still return success (security: no email enumeration)
            assert response.status_code == 200
            data = response.json()
            assert "message" in data

    @pytest.mark.asyncio
    async def test_magic_link_flow_expired_token(self, client, test_user_data, auth_headers):
        """Test flow with expired token."""
        from src.app.modules.magic_link.exceptions import InvalidMagicLinkError

        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
            side_effect=InvalidMagicLinkError("Token expired or invalid"),
        ):
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "expired_token_" + "a" * 29},
                headers=auth_headers,
            )

            assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_magic_link_flow_reused_token(self, client, test_user_data, auth_headers):
        """Test flow with already-used token."""
        from src.app.modules.magic_link.exceptions import MagicLinkAlreadyUsedError

        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
            side_effect=MagicLinkAlreadyUsedError("Token already used"),
        ):
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "reused_token_" + "a" * 28},
                headers=auth_headers,
            )

            assert response.status_code == 410


class TestMagicLinkSecurityConstraints:
    """Test security constraints per specifications."""

    def test_magic_link_token_length(self):
        """Verify token length matches security requirements (43 chars)."""
        from src.app.core.security.tokens import TokenGenerator

        token = TokenGenerator.generate_magic_link_token()
        # URL-safe base64 of 32 bytes = 43 characters
        assert len(token) == 43

    def test_magic_link_token_uniqueness(self):
        """Verify generated tokens are unique."""
        from src.app.core.security.tokens import TokenGenerator

        tokens = [TokenGenerator.generate_magic_link_token() for _ in range(100)]
        assert len(set(tokens)) == 100  # All unique

    @pytest.mark.asyncio
    async def test_magic_link_single_use(self):
        """Verify tokens can only be used once (consumed on first use)."""
        from src.app.core.security.tokens import MagicLinkManager
        from tests.conftest import FakeRedis

        redis = FakeRedis()
        manager = MagicLinkManager(redis=redis)

        # Create token
        token = await manager.create_token(
            email="test@example.com",
            action="login",
        )

        # First validation should succeed
        result = await manager.validate_token(token)
        assert result is not None
        assert result["email"] == "test@example.com"

        # Second validation should fail (token consumed)
        result = await manager.validate_token(token)
        assert result is None

    def test_magic_link_expiry_is_15_minutes(self):
        """Verify token expiry is set to 15 minutes per spec."""
        from src.app.core.security.tokens import MagicLinkManager

        assert MagicLinkManager.TOKEN_EXPIRY == 900  # 15 minutes in seconds
