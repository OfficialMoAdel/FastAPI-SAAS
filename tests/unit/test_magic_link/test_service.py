"""Unit tests for MagicLinkService.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T020 [US1] Write unit tests for MagicLinkService
"""

from unittest.mock import AsyncMock, patch

import pytest

# Import FakeRedis from conftest for Redis mocking
from tests.conftest import FakeRedis


class TestMagicLinkSchemas:
    """Test Pydantic schemas for magic link operations."""

    def test_magic_link_send_request_schema(self):
        """Verify MagicLinkSendRequest schema validates email."""
        from src.app.modules.magic_link.schemas import MagicLinkSendRequest

        # Valid email
        request = MagicLinkSendRequest(email="user@example.com")
        assert request.email == "user@example.com"

    def test_magic_link_send_request_rejects_invalid_email(self):
        """Verify MagicLinkSendRequest rejects invalid emails."""
        from pydantic import ValidationError

        from src.app.modules.magic_link.schemas import MagicLinkSendRequest

        with pytest.raises(ValidationError):
            MagicLinkSendRequest(email="not-an-email")

    def test_magic_link_send_response_schema(self):
        """Verify MagicLinkSendResponse has required fields."""
        from src.app.modules.magic_link.schemas import MagicLinkSendResponse

        response = MagicLinkSendResponse(
            message="If an account exists, a magic link has been sent",
            may_be_delayed=False,
        )
        assert response.message is not None
        assert response.may_be_delayed is False

    def test_magic_link_verify_request_schema(self):
        """Verify MagicLinkVerifyRequest schema validates token."""
        from src.app.modules.magic_link.schemas import MagicLinkVerifyRequest

        # Valid token (43 chars URL-safe base64)
        token = "a" * 43
        request = MagicLinkVerifyRequest(token=token)
        assert request.token == token


class TestMagicLinkService:
    """Test MagicLinkService business logic."""

    @pytest.fixture
    def fake_redis(self):
        """Create a fake Redis instance for testing."""
        return FakeRedis()

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_user(self):
        """Create a mock user dict for testing."""
        return {
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "is_active": True,
            "is_deleted": False,
            "tier_id": 1,
            "tier": {"name": "basic"},
        }

    def test_magic_link_service_exists(self):
        """Verify MagicLinkService can be imported."""
        from src.app.modules.magic_link.service import MagicLinkService

        assert MagicLinkService is not None

    @pytest.mark.asyncio
    async def test_send_magic_link_creates_token(self, fake_redis, mock_db, mock_user):
        """Verify send_magic_link creates and stores a token."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        # Mock user lookup
        with patch.object(service, "_get_user_by_email", return_value=mock_user):
            result = await service.send_magic_link(
                email="test@example.com",
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
            )

        assert result is not None
        assert result.message is not None

    @pytest.mark.asyncio
    async def test_send_magic_link_queues_email(self, fake_redis, mock_db, mock_user):
        """Verify send_magic_link queues email for delivery."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        with (
            patch.object(service, "_get_user_by_email", return_value=mock_user),
            patch.object(service, "_queue_magic_link_email", new_callable=AsyncMock) as mock_queue,
        ):
            await service.send_magic_link(
                email="test@example.com",
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
            )

            mock_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_magic_link_no_user_still_returns_success(self, fake_redis, mock_db):
        """Verify send_magic_link returns success even if user doesn't exist (security)."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        # User not found
        with patch.object(service, "_get_user_by_email", return_value=None):
            result = await service.send_magic_link(
                email="nonexistent@example.com",
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
            )

        # Should return success to prevent email enumeration
        assert result is not None
        assert result.message is not None

    @pytest.mark.asyncio
    async def test_verify_magic_link_returns_tokens(self, fake_redis, mock_db, mock_user):
        """Verify verify_magic_link returns auth tokens for valid token."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        # Store a valid token in fake redis
        token = "test_token_12345678901234567890123456789012"
        import json

        await fake_redis.setex(
            f"magic_link:{token}",
            900,
            json.dumps({"email": "test@example.com", "action": "login"}),
        )

        with (
            patch.object(service, "_get_user_by_email", return_value=mock_user),
            patch.object(
                service,
                "_create_auth_tokens",
                return_value={
                    "access_token": "access_123",
                    "refresh_token": "refresh_456",
                    "token_type": "bearer",
                    "expires_in": 3600,
                },
            ),
        ):
            result = await service.verify_magic_link(token=token)

        assert result is not None
        assert result.access_token is not None
        assert result.refresh_token is not None

    @pytest.mark.asyncio
    async def test_verify_magic_link_invalid_token_raises(self, fake_redis, mock_db):
        """Verify verify_magic_link raises error for invalid token."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        with pytest.raises(Exception):  # Should raise specific exception
            await service.verify_magic_link(token="invalid_token_not_in_redis")

    @pytest.mark.asyncio
    async def test_verify_magic_link_marks_token_as_used(self, fake_redis, mock_db, mock_user):
        """Verify token is consumed (deleted) after successful verification."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        # Store a valid token
        token = "test_token_12345678901234567890123456789012"
        import json

        await fake_redis.setex(
            f"magic_link:{token}",
            900,
            json.dumps({"email": "test@example.com", "action": "login"}),
        )

        with (
            patch.object(service, "_get_user_by_email", return_value=mock_user),
            patch.object(
                service,
                "_create_auth_tokens",
                return_value={
                    "access_token": "access_123",
                    "refresh_token": "refresh_456",
                    "token_type": "bearer",
                    "expires_in": 3600,
                },
            ),
        ):
            await service.verify_magic_link(token=token)

        # Token should be consumed (deleted from redis)
        assert await fake_redis.get(f"magic_link:{token}") is None

    @pytest.mark.asyncio
    async def test_verify_magic_link_rejects_used_token(self, fake_redis, mock_db, mock_user):
        """Verify already-used tokens are rejected."""
        from src.app.modules.magic_link.service import MagicLinkService

        service = MagicLinkService(db=mock_db, redis=fake_redis)

        # Store and consume a token
        token = "test_token_12345678901234567890123456789012"
        import json

        await fake_redis.setex(
            f"magic_link:{token}",
            900,
            json.dumps({"email": "test@example.com", "action": "login"}),
        )

        with (
            patch.object(service, "_get_user_by_email", return_value=mock_user),
            patch.object(
                service,
                "_create_auth_tokens",
                return_value={
                    "access_token": "access_123",
                    "refresh_token": "refresh_456",
                    "token_type": "bearer",
                    "expires_in": 3600,
                },
            ),
        ):
            # First verification should succeed
            await service.verify_magic_link(token=token)

        # Second verification should fail (token consumed)
        with pytest.raises(Exception):
            await service.verify_magic_link(token=token)


class TestMagicLinkTokenExpiry:
    """Test token expiration behavior."""

    @pytest.fixture
    def fake_redis(self):
        return FakeRedis()

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_token_expires_after_15_minutes(self, fake_redis, mock_db):
        """Verify tokens expire after 15 minutes (per spec)."""
        from src.app.core.security.tokens import MagicLinkManager

        manager = MagicLinkManager(redis=fake_redis)

        # Token expiry should be 900 seconds (15 minutes)
        assert manager.TOKEN_EXPIRY == 900
