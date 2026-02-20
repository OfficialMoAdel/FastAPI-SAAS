"""Unit tests for secure token generation utilities.

Tests follow TDD approach - these tests define the expected behavior
for the TokenGenerator class implementation.
"""

from unittest.mock import AsyncMock

import pytest


class TestTokenGenerator:
    """Test secure token generation utilities."""

    def test_magic_link_token_length(self):
        """Test that magic link tokens have correct length."""
        from src.app.core.security.tokens import TokenGenerator

        token = TokenGenerator.generate_magic_link_token()

        # 32 bytes = 43 base64 characters
        assert len(token) == 43
        # Should be URL-safe base64
        assert all(c.isalnum() or c in "-_" for c in token)

    def test_magic_link_token_uniqueness(self):
        """Test that magic link tokens are unique."""
        from src.app.core.security.tokens import TokenGenerator

        tokens = {TokenGenerator.generate_magic_link_token() for _ in range(100)}

        assert len(tokens) == 100  # All unique

    def test_sso_state_length(self):
        """Test that SSO state tokens have correct length."""
        from src.app.core.security.tokens import TokenGenerator

        state = TokenGenerator.generate_sso_state()

        # 16 bytes = 22 base64 characters
        assert len(state) == 22

    def test_verification_token_length(self):
        """Test that verification tokens have correct length."""
        from src.app.core.security.tokens import TokenGenerator

        token = TokenGenerator.generate_verification_token()

        # 32 bytes = 43 base64 characters
        assert len(token) == 43

    def test_secure_password_default_length(self):
        """Test secure password generation with default length."""
        from src.app.core.security.tokens import TokenGenerator

        password = TokenGenerator.generate_secure_password()

        # 32 bytes = 43 base64 characters
        assert len(password) == 43

    def test_secure_password_custom_length(self):
        """Test secure password generation with custom length."""
        from src.app.core.security.tokens import TokenGenerator

        password = TokenGenerator.generate_secure_password(length=16)

        # 16 bytes = 22 base64 characters
        assert len(password) == 22

    def test_api_key_format(self):
        """Test API key generation with prefix."""
        from src.app.core.security.tokens import TokenGenerator

        key = TokenGenerator.generate_api_key(prefix="sk_live")

        assert key.startswith("sk_live_")
        # prefix (7) + underscore (1) + 43 chars = 51
        assert len(key) == 51

    def test_api_key_default_prefix(self):
        """Test API key generation with default prefix."""
        from src.app.core.security.tokens import TokenGenerator

        key = TokenGenerator.generate_api_key()

        assert key.startswith("sk_live_")


class TestSSOStateAsync:
    """Test async SSO state management with Redis."""

    @pytest.mark.asyncio
    async def test_store_sso_state_in_redis(self):
        """Test that SSO state is stored in Redis with expiration."""
        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        manager = SSOStateManager(redis=mock_redis)

        state = await manager.create_state(provider="google", redirect_uri="https://app.example.com/callback")

        # Verify state was generated
        assert state is not None
        assert len(state) == 22

        # Verify Redis setex was called with 600 seconds (10 minutes)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 600  # 10 minutes TTL

    @pytest.mark.asyncio
    async def test_validate_sso_state_success(self):
        """Test that valid SSO state is accepted once."""
        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        # Mock eval() for GETDEL Lua script (atomic get-and-delete)
        mock_redis.eval.return_value = b'{"provider": "google", "redirect_uri": "https://app.example.com/callback"}'

        manager = SSOStateManager(redis=mock_redis)

        result = await manager.validate_state("valid_state")

        assert result is not None
        assert result["provider"] == "google"
        # eval() should be called for atomic GETDEL
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_sso_state_expired(self):
        """Test that expired SSO state is rejected."""
        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # State doesn't exist (expired or used)

        manager = SSOStateManager(redis=mock_redis)

        result = await manager.validate_state("expired_state")

        assert result is None
        # Delete should not be called for non-existent state
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_sso_state_one_time_use(self):
        """Test that SSO state can only be used once."""
        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        # First eval() call returns data (GETDEL), second returns None (key deleted)
        mock_redis.eval.side_effect = [b'{"provider": "google"}', None]

        manager = SSOStateManager(redis=mock_redis)

        # First validation should succeed
        result1 = await manager.validate_state("test_state")
        assert result1 is not None

        # Second validation should fail (state was deleted)
        result2 = await manager.validate_state("test_state")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_sso_state_stores_metadata(self):
        """Test that SSO state stores provider and redirect metadata."""
        import json

        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        manager = SSOStateManager(redis=mock_redis)

        await manager.create_state(
            provider="github", redirect_uri="https://app.example.com/github/callback", extra_data={"org_id": "123"}
        )

        # Verify the stored data includes metadata
        call_args = mock_redis.setex.call_args
        stored_value = call_args[0][2]
        stored_data = json.loads(stored_value)

        assert stored_data["provider"] == "github"
        assert stored_data["redirect_uri"] == "https://app.example.com/github/callback"
        assert stored_data["extra_data"]["org_id"] == "123"


class TestMagicLinkManager:
    """Test async magic link token management."""

    @pytest.mark.asyncio
    async def test_create_magic_link_stores_in_redis(self):
        """Test that magic link token is stored in Redis."""
        from src.app.core.security.tokens import MagicLinkManager

        mock_redis = AsyncMock()
        manager = MagicLinkManager(redis=mock_redis)

        token = await manager.create_token(email="user@example.com", action="login")

        assert token is not None
        assert len(token) == 43

        # Verify Redis setex was called with 900 seconds (15 minutes)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 900  # 15 minutes TTL

    @pytest.mark.asyncio
    async def test_validate_magic_link_success(self):
        """Test that valid magic link is accepted."""
        from src.app.core.security.tokens import MagicLinkManager

        mock_redis = AsyncMock()
        # Mock eval() for GETDEL Lua script (atomic get-and-delete)
        mock_redis.eval.return_value = b'{"email": "user@example.com", "action": "login"}'

        manager = MagicLinkManager(redis=mock_redis)

        result = await manager.validate_token("valid_token")

        assert result is not None
        assert result["email"] == "user@example.com"
        assert result["action"] == "login"
        # eval() should be called for atomic GETDEL
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_magic_link_expired(self):
        """Test that expired magic link is rejected."""
        from src.app.core.security.tokens import MagicLinkManager

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = MagicLinkManager(redis=mock_redis)

        result = await manager.validate_token("expired_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_magic_link_one_time_use(self):
        """Test that magic link can only be used once."""
        from src.app.core.security.tokens import MagicLinkManager

        mock_redis = AsyncMock()
        # First eval() call returns data (GETDEL), second returns None (key deleted)
        mock_redis.eval.side_effect = [
            b'{"email": "user@example.com"}',
            None,  # Deleted after first use
        ]

        manager = MagicLinkManager(redis=mock_redis)

        result1 = await manager.validate_token("test_token")
        assert result1 is not None

        result2 = await manager.validate_token("test_token")
        assert result2 is None


class TestDependencyInjection:
    """Test dependency injection support for token managers."""

    @pytest.mark.asyncio
    async def test_get_sso_state_manager(self):
        """Test that get_sso_state_manager returns proper instance."""
        from src.app.core.security.tokens import SSOStateManager, get_sso_state_manager

        # Mock Redis dependency
        mock_redis = AsyncMock()

        manager = get_sso_state_manager(redis=mock_redis)

        assert isinstance(manager, SSOStateManager)

    @pytest.mark.asyncio
    async def test_get_magic_link_manager(self):
        """Test that get_magic_link_manager returns proper instance."""
        from src.app.core.security.tokens import MagicLinkManager, get_magic_link_manager

        mock_redis = AsyncMock()

        manager = get_magic_link_manager(redis=mock_redis)

        assert isinstance(manager, MagicLinkManager)


class TestTokenAuditLogging:
    """Test audit logging for token operations."""

    @pytest.mark.asyncio
    async def test_sso_state_creation_logged(self, caplog):
        """Test that SSO state creation is logged."""
        import logging

        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        manager = SSOStateManager(redis=mock_redis)

        with caplog.at_level(logging.DEBUG, logger="src.app.core.security.tokens"):
            await manager.create_state(provider="google", redirect_uri="https://example.com")

        assert any(
            "sso_state" in record.message.lower() or "create" in record.message.lower() for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_sso_state_validation_logged(self, caplog):
        """Test that SSO state validation is logged."""
        import logging

        from src.app.core.security.tokens import SSOStateManager

        mock_redis = AsyncMock()
        mock_redis.get.return_value = b'{"provider": "google"}'

        manager = SSOStateManager(redis=mock_redis)

        with caplog.at_level(logging.DEBUG, logger="src.app.core.security.tokens"):
            await manager.validate_state("test_state")

        assert any("validate" in record.message.lower() or "sso" in record.message.lower() for record in caplog.records)
