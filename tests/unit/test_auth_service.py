"""Unit tests for AuthService business logic edge cases.

Tests cover:
- Account lockout mechanism (brute force protection)
- Lockout expiration after timeout
- Successful login clearing lockout
- Token refresh edge cases
- Token expiration handling
- Logout edge cases
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def mock_response():
    """Mock FastAPI response."""
    return MagicMock()


@pytest.fixture
def mock_user():
    """Mock authenticated user."""
    return {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "is_superuser": False,
    }


class TestAuthServiceLockout:
    """Tests for account lockout (brute force protection)."""

    @pytest.mark.asyncio
    async def test_account_locked_after_5_failed_attempts(self, mock_db, mock_redis, mock_response):
        """Test that account is locked after 5 failed login attempts."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        # Simulate 5 failed attempts already recorded
        mock_redis.get = AsyncMock(return_value=b"5")

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.login("testuser", "wrongpassword", mock_response, mock_db, mock_redis)

        assert "Account locked" in str(exc_info.value.detail)
        assert "15 minutes" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_failed_attempt_increments_counter(self, mock_db, mock_redis, mock_response):
        """Test that failed login increments the attempt counter."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        mock_redis.get = AsyncMock(return_value=None)  # No previous attempts
        mock_redis.incr = AsyncMock(return_value=1)  # First attempt

        with (
            patch(
                "src.app.modules.auth.service.authenticate_user", new_callable=AsyncMock
            ) as mock_auth,
        ):
            mock_auth.return_value = None  # Authentication fails

            with pytest.raises(UnauthorizedException):
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_db, mock_redis)

            mock_redis.incr.assert_called_once()
            # First attempt sets expiration
            mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_lockout_counter_expires_after_15_minutes(self, mock_db, mock_redis, mock_response):
        """Test that lockout counter expiration is set to 15 minutes (900 seconds)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=1)

        with patch(
            "src.app.modules.auth.service.authenticate_user", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = None

            with pytest.raises(UnauthorizedException):
                await auth_service.login("testuser", "wrong", mock_response, mock_db, mock_redis)

            # Check 900 seconds (15 minutes) expiration
            mock_redis.expire.assert_called_with("login_attempts:testuser", 900)

    @pytest.mark.asyncio
    async def test_successful_login_clears_lockout_counter(
        self, mock_db, mock_redis, mock_response, mock_user
    ):
        """Test that successful login clears the failed attempt counter."""
        from src.app.modules.auth.service import auth_service

        mock_redis.get = AsyncMock(return_value=b"3")  # 3 previous failed attempts

        with (
            patch(
                "src.app.modules.auth.service.authenticate_user", new_callable=AsyncMock
            ) as mock_auth,
            patch(
                "src.app.modules.auth.service.create_access_token", new_callable=AsyncMock
            ) as mock_access,
            patch(
                "src.app.modules.auth.service.create_refresh_token", new_callable=AsyncMock
            ) as mock_refresh,
        ):
            mock_auth.return_value = mock_user
            mock_access.return_value = "access_token"
            mock_refresh.return_value = "refresh_token"

            await auth_service.login("testuser", "correct_password", mock_response, mock_db, mock_redis)

            # Verify counter was cleared
            mock_redis.delete.assert_called_once_with("login_attempts:testuser")

    @pytest.mark.asyncio
    async def test_lockout_at_exactly_5_attempts(self, mock_db, mock_redis, mock_response):
        """Test that lockout triggers at exactly 5 attempts."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        # 4 previous attempts - should NOT be locked
        mock_redis.get = AsyncMock(return_value=b"4")

        with patch(
            "src.app.modules.auth.service.authenticate_user", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = None  # Auth fails

            # This is the 5th attempt - should fail but not be locked yet
            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.login("testuser", "wrong", mock_response, mock_db, mock_redis)

            # Should show "wrong password" not "locked" (becomes locked AFTER this attempt)
            assert "Wrong username" in str(exc_info.value.detail)


class TestAuthServiceTokenRefresh:
    """Tests for token refresh edge cases."""

    @pytest.mark.asyncio
    async def test_refresh_with_missing_token_raises_error(self, mock_db):
        """Test that refresh with no token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.refresh_access_token(None, mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_empty_string_token_raises_error(self, mock_db):
        """Test that refresh with empty string token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.refresh_access_token("", mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token_raises_error(self, mock_db):
        """Test that refresh with invalid/expired token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        with patch(
            "src.app.modules.auth.service.verify_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = None  # Token invalid or expired

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("expired_token", mock_db)

            assert "Invalid refresh token" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_success_returns_new_access_token(self, mock_db):
        """Test successful token refresh returns new access token."""
        from src.app.modules.auth.service import auth_service

        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "testuser"

        with (
            patch(
                "src.app.modules.auth.service.verify_token", new_callable=AsyncMock
            ) as mock_verify,
            patch(
                "src.app.modules.auth.service.create_access_token", new_callable=AsyncMock
            ) as mock_create,
        ):
            mock_verify.return_value = mock_token_data
            mock_create.return_value = "new_access_token"

            result = await auth_service.refresh_access_token("valid_refresh_token", mock_db)

            assert result["access_token"] == "new_access_token"
            assert result["token_type"] == "bearer"


class TestAuthServiceLogout:
    """Tests for logout edge cases."""

    @pytest.mark.asyncio
    async def test_logout_without_refresh_token_raises_error(self, mock_db, mock_response):
        """Test that logout without refresh token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.logout("access_token", None, mock_response, mock_db)

        assert "Refresh token not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_logout_success_blacklists_tokens(self, mock_db, mock_response):
        """Test successful logout blacklists both tokens and clears cookie."""
        from src.app.modules.auth.service import auth_service

        with patch(
            "src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock
        ) as mock_blacklist:
            result = await auth_service.logout("access_token", "refresh_token", mock_response, mock_db)

            mock_blacklist.assert_called_once_with(
                access_token="access_token", refresh_token="refresh_token", db=mock_db
            )
            mock_response.delete_cookie.assert_called_once_with(key="refresh_token")
            assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_with_invalid_jwt_raises_error(self, mock_db, mock_response):
        """Test that logout with JWT decode error raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.auth.service import auth_service

        with patch(
            "src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock
        ) as mock_blacklist:
            mock_blacklist.side_effect = JWTError("Invalid token")

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.logout("invalid_token", "refresh_token", mock_response, mock_db)

            assert "Invalid token" in str(exc_info.value.detail)
