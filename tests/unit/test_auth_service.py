"""Unit tests for AuthService business logic edge cases.

Tests cover:
- Account lockout mechanism (brute force protection)
- Lockout expiration after timeout
- Successful login clearing lockout
- Token refresh edge cases
- Token expiration handling
- Logout edge cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError

from src.app.modules.auth.service import AuthService


@pytest.fixture
def auth_service():
    """Create an AuthService instance."""
    return AuthService()


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
def mock_request():
    """Mock FastAPI request for audit logging."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = MagicMock()
    request.headers.get = MagicMock(return_value="test-user-agent")
    return request


@pytest.fixture
def mock_user():
    """Mock authenticated user."""
    return {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "is_superuser": False,
        "tenant_id": 1,
    }


class TestAuthServiceLockout:
    """Tests for account lockout (brute force protection)."""

    @pytest.mark.asyncio
    async def test_account_locked_after_5_failed_attempts(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request
    ):
        """Test that account is locked after 5 failed login attempts."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        # Simulate 5 failed attempts already recorded
        mock_redis.get = AsyncMock(return_value=b"5")

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

        assert "Account locked" in str(exc_info.value.detail)
        assert "15 minutes" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_failed_attempt_increments_counter(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request
    ):
        """Test that failed login increments the attempt counter."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_redis.get = AsyncMock(return_value=None)  # No previous attempts
        mock_redis.incr = AsyncMock(return_value=1)  # First attempt

        with (
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
        ):
            mock_auth.return_value = None  # Authentication fails

            with pytest.raises(UnauthorizedException):
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

            mock_redis.incr.assert_called_once()
            # First attempt sets expiration
            mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_lockout_counter_expires_after_15_minutes(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request
    ):
        """Test that lockout counter expiration is set to 15 minutes (900 seconds)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=1)

        with patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = None

            with pytest.raises(UnauthorizedException):
                await auth_service.login("testuser", "wrong", mock_response, mock_request, mock_db, mock_redis)

            # Check 900 seconds (15 minutes) expiration
            mock_redis.expire.assert_called_with("login_attempts:testuser", 900)

    @pytest.mark.asyncio
    async def test_successful_login_clears_lockout_counter(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request, mock_user
    ):
        """Test that successful login clears the failed attempt counter."""
        mock_redis.get = AsyncMock(return_value=b"3")  # 3 previous failed attempts

        with (
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
            patch("src.app.modules.auth.service.create_access_token", new_callable=AsyncMock) as mock_access,
            patch("src.app.modules.auth.service.create_refresh_token", new_callable=AsyncMock) as mock_refresh,
        ):
            mock_auth.return_value = mock_user
            mock_access.return_value = "access_token"
            mock_refresh.return_value = "refresh_token"

            await auth_service.login("testuser", "correct_password", mock_response, mock_request, mock_db, mock_redis)

            # Verify counter was cleared
            mock_redis.delete.assert_called_once_with("login_attempts:testuser")

    @pytest.mark.asyncio
    async def test_lockout_at_exactly_5_attempts(self, auth_service, mock_db, mock_redis, mock_response, mock_request):
        """Test that lockout triggers at exactly 5 attempts."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        # 4 previous attempts - should NOT be locked
        mock_redis.get = AsyncMock(return_value=b"4")

        with patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = None  # Auth fails

            # This is the 5th attempt - should fail but not be locked yet
            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.login("testuser", "wrong", mock_response, mock_request, mock_db, mock_redis)

            # Should show "wrong password" not "locked" (becomes locked AFTER this attempt)
            assert "Wrong username" in str(exc_info.value.detail)


class TestAuthServiceTokenRefresh:
    """Tests for token refresh edge cases."""

    @pytest.mark.asyncio
    async def test_refresh_with_missing_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with no token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.refresh_access_token(None, mock_response, mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_empty_string_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with empty string token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.refresh_access_token("", mock_response, mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with invalid/expired token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = None  # Token invalid or expired

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("expired_token", mock_response, mock_db)

            assert "Invalid refresh token" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_success_rotates_tokens(self, auth_service, mock_db, mock_response):
        """Test successful token refresh rotates both tokens and sets cookie."""
        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "testuser"

        # Mock settings to simulate production environment
        with (
            patch("src.app.modules.auth.service.settings") as mock_settings,
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.create_access_token", new_callable=AsyncMock) as mock_create_access,
            patch("src.app.modules.auth.service.create_refresh_token", new_callable=AsyncMock) as mock_create_refresh,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock) as mock_blacklist,
            patch("src.app.modules.auth.service.crud_users") as mock_crud_users,
        ):
            from src.app.core.config import EnvironmentOption

            mock_settings.ENVIRONMENT = EnvironmentOption.PRODUCTION
            mock_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

            mock_verify.return_value = mock_token_data
            mock_create_access.return_value = "new_access_token"
            mock_create_refresh.return_value = "new_refresh_token"
            mock_crud_users.get_user_with_tier_eager = AsyncMock(
                return_value={"tier_id": 1, "username": "testuser", "tier": {"name": "basic"}}
            )

            result = await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            # Check new access token returned
            assert result["access_token"] == "new_access_token"
            assert result["token_type"] == "bearer"

            # Verify Rotation Logic
            # 1. Old token blacklisted
            mock_blacklist.assert_called_once_with(token="valid_refresh_token", db=mock_db)

            # 2. New refresh token created
            mock_create_refresh.assert_called_once()

            # 3. New cookie set
            mock_response.set_cookie.assert_called_once()
            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["key"] == "refresh_token"
            assert call_kwargs["value"] == "new_refresh_token"
            assert call_kwargs["httponly"] is True
            assert call_kwargs["secure"] is True
            assert call_kwargs["samesite"] == "strict"


class TestAuthServiceLogout:
    """Tests for logout edge cases."""

    @pytest.mark.asyncio
    async def test_logout_without_refresh_token_raises_error(self, auth_service, mock_db, mock_response, mock_request):
        """Test that logout without refresh token raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.logout("access_token", None, mock_response, mock_request, mock_db)

        assert "Refresh token not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_logout_success_blacklists_tokens(self, auth_service, mock_db, mock_response, mock_request):
        """Test successful logout blacklists both tokens and clears cookie."""
        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock) as mock_blacklist:
            result = await auth_service.logout("access_token", "refresh_token", mock_response, mock_request, mock_db)

            mock_blacklist.assert_called_once_with(
                access_token="access_token", refresh_token="refresh_token", db=mock_db
            )
            mock_response.delete_cookie.assert_called_once_with(key="refresh_token")
            assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_with_invalid_jwt_raises_error(self, auth_service, mock_db, mock_response, mock_request):
        """Test that logout with JWT decode error raises UnauthorizedException."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock) as mock_blacklist:
            mock_blacklist.side_effect = JWTError("Invalid token")

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.logout("invalid_token", "refresh_token", mock_response, mock_request, mock_db)

            assert "Invalid token" in str(exc_info.value.detail)


class TestAuthServiceEmailFlows:
    """Tests for email-related flows (password reset, verification)."""

    @pytest.mark.asyncio
    async def test_request_password_reset_sends_email(self, auth_service, mock_db):
        """Test that requesting password reset triggers email event."""
        mock_user = {"id": 1, "email": "test@example.com", "username": "testuser", "tenant_id": 100}

        with (
            patch("src.app.modules.users.crud.crud_users.get", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.core.events.event_bus.publish", new_callable=AsyncMock) as mock_publish,
            patch("src.app.modules.auth.models.PasswordResetToken") as mock_token_model,
            patch("src.app.modules.auth.service.settings") as mock_settings,
        ):
            mock_get_user.return_value = mock_user
            mock_settings.FRONTEND_URL = "http://test-frontend"

            # Mock the session.add and commit behavior
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()

            # Mock token creation
            mock_token = MagicMock()
            mock_token.token = "reset_token_123"
            mock_token.id = 55
            mock_token_model.return_value = mock_token

            await auth_service.request_password_reset("test@example.com", mock_db)

            # Check user lookup
            mock_get_user.assert_called_once_with(db=mock_db, email="test@example.com", is_deleted=False)

            # Check token saved
            mock_db.add.assert_called()
            mock_db.commit.assert_called()

            # Check event published
            mock_publish.assert_called_once()
            call_args = mock_publish.call_args
            assert call_args[0][0] == "notification.send"
            assert call_args[0][1]["template_name"] == "password_reset"
            assert call_args[0][1]["to_email"] == "test@example.com"
            assert "reset_link" in call_args[0][1]["context"]
            assert "http://test-frontend" in call_args[0][1]["context"]["reset_link"]

    @pytest.mark.asyncio
    async def test_send_verification_email_creates_token_and_event(self, auth_service, mock_db):
        """Test sending verification email creates token and triggers event."""
        mock_user = {
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "is_email_verified": False,
            "tenant_id": 100,
        }

        with (
            patch("src.app.modules.users.crud.crud_users.get", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.core.events.event_bus.publish", new_callable=AsyncMock) as mock_publish,
            patch("src.app.modules.auth.models.EmailVerificationToken") as mock_token_cls,
            patch("src.app.modules.auth.service.settings") as mock_settings,
        ):
            mock_get_user.return_value = mock_user
            mock_settings.FRONTEND_URL = "http://test-frontend"

            mock_token_instance = MagicMock()
            mock_token_instance.token = "verify_token_123"
            mock_token_cls.return_value = mock_token_instance

            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()

            await auth_service.send_verification_email("test@example.com", mock_db)

            # Check token created and stored
            mock_token_cls.assert_called_with(user_id=1)
            mock_db.add.assert_called_with(mock_token_instance)
            mock_db.commit.assert_called()

            # Check event published
            mock_publish.assert_called_once()
            call_args = mock_publish.call_args
            assert call_args[0][0] == "notification.send"
            assert call_args[0][1]["template_name"] == "email_verification"
            assert "verification_link" in call_args[0][1]["context"]

    @pytest.mark.asyncio
    async def test_verify_email_validates_and_updates_user(self, auth_service, mock_db):
        """Test verification logic updates user and deletes token."""
        mock_user = {"id": 99, "is_email_verified": False}

        with (
            patch("src.app.modules.users.crud.crud_users.get", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.users.crud.crud_users.update", new_callable=AsyncMock) as mock_update_user,
        ):
            mock_get_user.return_value = mock_user

            # Create a mock token object returned by DB query
            mock_token = MagicMock()
            mock_token.is_expired = False
            mock_token.user_id = 99

            # Mock DB Query results
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_token

            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.delete = AsyncMock()
            mock_db.commit = AsyncMock()

            result = await auth_service.verify_email("verify_token_123", mock_db)

            # Assertions
            mock_get_user.assert_called_once()
            mock_update_user.assert_called_once()
            mock_db.delete.assert_called_once_with(mock_token)
            mock_db.commit.assert_called()
            assert result == {"message": "Email verified successfully."}

    @pytest.mark.asyncio
    async def test_verify_email_invalid_token_raises_error(self, auth_service, mock_db):
        """Test verify_email raises error if token is invalid or missing."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.verify_email("invalid_token", mock_db)

        assert "Invalid verification token" in str(exc_info.value.detail)


class TestAuthServiceSecurityBugFixes:
    """Tests for A1-A3 auth bug fixes (PHASE-AUTH-BUGS).

    A1: Token refresh for deleted users (SECURITY CRITICAL)
    A2: Missing is_email_verified in cached auth info
    A3: AttributeError in update_tier with dict response
    """

    @pytest.mark.asyncio
    async def test_a1_token_refresh_blocks_deleted_users(self, auth_service, mock_db, mock_response):
        """A1 SECURITY: Token refresh must reject deleted users."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "deleteduser"

        with (
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.get_user_service") as mock_get_user_service,
        ):
            mock_verify.return_value = mock_token_data

            # Simulate deleted user in cache
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(
                return_value={
                    "id": 1,
                    "username": "deleteduser",
                    "is_deleted": True,  # User was deleted
                    "tier_id": 1,
                    "tier": {"name": "basic"},
                }
            )
            mock_get_user_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert "no longer active" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_a1_token_refresh_blocks_nonexistent_users(self, auth_service, mock_db, mock_response):
        """A1 SECURITY: Token refresh must reject users that no longer exist."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "ghostuser"

        with (
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.get_user_service") as mock_get_user_service,
        ):
            mock_verify.return_value = mock_token_data

            # User not found (returns None)
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(return_value=None)
            mock_get_user_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert "no longer active" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_a1_token_refresh_allows_active_users(self, auth_service, mock_db, mock_response):
        """A1: Token refresh should work for active (non-deleted) users."""
        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "activeuser"

        with (
            patch("src.app.modules.auth.service.settings") as mock_settings,
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.create_access_token", new_callable=AsyncMock) as mock_create_access,
            patch("src.app.modules.auth.service.create_refresh_token", new_callable=AsyncMock) as mock_create_refresh,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.get_user_service") as mock_get_user_service,
        ):
            from src.app.core.config import EnvironmentOption

            mock_settings.ENVIRONMENT = EnvironmentOption.LOCAL
            mock_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7
            mock_verify.return_value = mock_token_data
            mock_create_access.return_value = "new_access_token"
            mock_create_refresh.return_value = "new_refresh_token"

            # Active user (is_deleted=False)
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(
                return_value={
                    "id": 1,
                    "username": "activeuser",
                    "is_deleted": False,
                    "is_email_verified": True,
                    "tier_id": 1,
                    "tier": {"name": "premium"},
                }
            )
            mock_get_user_service.return_value = mock_user_service

            result = await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert result["access_token"] == "new_access_token"
            assert result["token_type"] == "bearer"
