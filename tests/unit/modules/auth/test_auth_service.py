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
from uuid import uuid4

import pytest
from jwt.exceptions import PyJWTError as JWTError

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
    redis.eval = AsyncMock(return_value=1)  # Default: first attempt, not locked
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
        "is_active": True,
        "tenant_id": uuid4(),
    }


@pytest.fixture
def mock_tenant():
    """Mock tenant for tenant context."""
    tenant = MagicMock()
    tenant.id = uuid4()
    return tenant


class TestAuthServiceLockout:
    """Tests for account lockout (brute force protection)."""

    @pytest.mark.asyncio
    async def test_account_locked_after_5_failed_attempts(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request, mock_tenant
    ):
        """Test that account is locked after 5 failed login attempts."""
        from src.app.modules._base.exceptions import UnauthorizedError

        # Lua script returns -1 when account is locked
        mock_redis.eval = AsyncMock(return_value=-1)

        with (
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
            patch(
                "src.app.modules.users.service.UserService.get_user_auth_info", new_callable=AsyncMock
            ) as mock_get_user,
        ):
            mock_auth.return_value = None  # Authentication fails
            mock_get_user.return_value = {"id": 1, "tenant_id": mock_tenant.id}

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

            # SECURITY: Login uses generic error message to prevent username enumeration
            # The lockout is enforced (returns -1), but error message is generic
            assert "Wrong username" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_failed_attempt_increments_counter(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request
    ):
        """Test that failed login calls Lua script for atomic increment."""
        from src.app.modules._base.exceptions import UnauthorizedError

        # Lua script returns 1 (first attempt, not locked)
        mock_redis.eval = AsyncMock(return_value=1)

        with (
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
        ):
            mock_auth.return_value = None  # Authentication fails

            with pytest.raises(UnauthorizedError):
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

            # Lua script was called (handles check + increment + expire atomically)
            mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_lockout_counter_expires_after_15_minutes(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request
    ):
        """Test that Lua script receives correct lockout duration (900 seconds)."""
        from src.app.modules._base.exceptions import UnauthorizedError

        mock_redis.eval = AsyncMock(return_value=1)

        with patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = None

            with pytest.raises(UnauthorizedError):
                await auth_service.login("testuser", "wrong", mock_response, mock_request, mock_db, mock_redis)

            # Check Lua script received 900 seconds as lockout duration arg
            call_args = mock_redis.eval.call_args
            assert call_args[0][4] == "900"  # ARGV[2] = lockout duration (str)

    @pytest.mark.asyncio
    async def test_successful_login_clears_lockout_counter(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request, mock_user
    ):
        """Test that successful login clears the failed attempt counter."""
        # Lua script returns 3 (not locked)
        mock_redis.eval = AsyncMock(return_value=3)

        mock_mfa_service = MagicMock()
        mock_mfa_service.is_mfa_enabled = AsyncMock(return_value=False)

        with (
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
            patch("src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock) as mock_access,
            patch("src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock) as mock_refresh,
            patch("src.app.modules.auth.services.mfa.get_mfa_service", return_value=mock_mfa_service),
            # S-4: session registration is orthogonal to lockout logic; it now
            # fails closed without Redis, so mock it out in this unit test.
            # Patch at the tokens.py lookup site (module-level import there).
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
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
        from src.app.modules._base.exceptions import UnauthorizedError

        # Lua script returns 5 (5th attempt just recorded, not yet locked)
        mock_redis.eval = AsyncMock(return_value=5)

        with patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = None  # Auth fails

            # This is the 5th attempt - should fail but not be locked yet
            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.login("testuser", "wrong", mock_response, mock_request, mock_db, mock_redis)

            # Should show "wrong password" not "locked" (becomes locked AFTER this attempt)
            assert "Wrong username" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_lockout_works_when_eval_returns_bytes(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request, mock_tenant
    ):
        """BUG FIX REGRESSION: Lockout must work even if redis.eval returns bytes.

        Without the int() coercion fix, b'-1' != -1, causing lockout to silently
        fail and allowing infinite brute-force attempts.
        """
        from src.app.modules._base.exceptions import UnauthorizedError

        # Simulate raw Redis bytes response (happens when decode_responses=False)
        mock_redis.eval = AsyncMock(return_value=b"-1")

        with (
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
            patch(
                "src.app.modules.users.service.UserService.get_user_auth_info", new_callable=AsyncMock
            ) as mock_get_user,
        ):
            mock_auth.return_value = None
            mock_get_user.return_value = {"id": 1, "tenant_id": mock_tenant.id}

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

            # Lock message should be returned even with bytes response
            assert "Wrong username" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_lockout_works_when_eval_returns_string(
        self, auth_service, mock_db, mock_redis, mock_response, mock_request, mock_tenant
    ):
        """BUG FIX REGRESSION: Lockout must work even if redis.eval returns str '-1'."""
        from src.app.modules._base.exceptions import UnauthorizedError

        mock_redis.eval = AsyncMock(return_value="-1")

        with (
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
            patch("src.app.modules.auth.service.AuthService.authenticate_user", new_callable=AsyncMock) as mock_auth,
            patch(
                "src.app.modules.users.service.UserService.get_user_auth_info", new_callable=AsyncMock
            ) as mock_get_user,
        ):
            mock_auth.return_value = None
            mock_get_user.return_value = {"id": 1, "tenant_id": mock_tenant.id}

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.login("testuser", "wrongpassword", mock_response, mock_request, mock_db, mock_redis)

            assert "Wrong username" in str(exc_info.value.detail)


class TestAuthServiceTokenRefresh:
    """Tests for token refresh edge cases."""

    @pytest.mark.asyncio
    async def test_refresh_with_missing_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with no token raises UnauthorizedException."""
        from src.app.modules._base.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError) as exc_info:
            await auth_service.refresh_access_token(None, mock_response, mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_empty_string_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with empty string token raises UnauthorizedException."""
        from src.app.modules._base.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError) as exc_info:
            await auth_service.refresh_access_token("", mock_response, mock_db)

        assert "Refresh token missing" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token_raises_error(self, auth_service, mock_db, mock_response):
        """Test that refresh with invalid/expired token raises UnauthorizedException."""
        from src.app.modules._base.exceptions import UnauthorizedError

        with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = None  # Token invalid or expired

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.refresh_access_token("expired_token", mock_response, mock_db)

            assert "Invalid refresh token" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_success_rotates_tokens(self, auth_service, mock_db, mock_response, mock_tenant):
        """Test successful token refresh rotates both tokens and sets cookie."""
        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "testuser"
        mock_token_data.absolute_exp = None  # Bypass session expiration check
        mock_token_data.token_version = 0  # Match db_user token_version

        # Mock settings to simulate production environment
        with (
            patch("src.app.modules.auth.service.settings") as mock_settings,
            patch("src.app.core.security.login_helpers.settings") as mock_helpers_settings,
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch(
                "src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock
            ) as mock_create_access,
            patch(
                "src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock
            ) as mock_create_refresh,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock) as mock_blacklist,
            patch("src.app.modules.auth.service.UserService") as mock_get_user_service,
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
        ):
            from src.app.core.config import EnvironmentOption

            mock_settings.ENVIRONMENT = EnvironmentOption.PRODUCTION
            mock_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7
            mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
            mock_helpers_settings.ENVIRONMENT = EnvironmentOption.PRODUCTION
            mock_helpers_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

            mock_verify.return_value = mock_token_data
            mock_create_access.return_value = "new_access_token"
            mock_create_refresh.return_value = "new_refresh_token"

            # Mock the UserService returned by get_user_service
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(
                return_value={
                    "id": 1,
                    "username": "testuser",
                    "tier_id": 1,
                    "tier": {"name": "basic"},
                    "is_deleted": False,
                    "is_active": True,
                    "token_version": 0,
                }
            )
            mock_get_user_service.return_value = mock_user_service

            result = await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            # Check new access token returned
            assert result["access_token"] == "new_access_token"
            assert result["token_type"] == "bearer"

            # Verify Rotation Logic
            # 1. Old token blacklisted
            mock_blacklist.assert_called_once_with(token="valid_refresh_token", db=mock_db)

            # 2. New refresh token created
            mock_create_refresh.assert_called_once()

            # 3. Service does NOT set cookies (P1-5: router owns cookie I/O;
            # the service returns refresh_token in the dict for the router).
            mock_response.set_cookie.assert_not_called()
            assert result["refresh_token"] == "new_refresh_token"


class TestAuthServiceLogout:
    """Tests for logout edge cases."""

    @pytest.mark.asyncio
    async def test_logout_without_refresh_token_raises_error(self, auth_service, mock_db, mock_response, mock_request):
        """A8: logout now accepts access-token-only clients. Only a call with
        NO tokens at all raises UnauthorizedException."""
        from src.app.modules._base.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError) as exc_info:
            await auth_service.logout(None, None, mock_response, mock_request, mock_db)

        assert "No token provided" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_logout_success_blacklists_tokens(self, auth_service, mock_db, mock_response, mock_request):
        """Test successful logout blacklists both tokens and clears cookie."""
        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock) as mock_blacklist:
            result = await auth_service.logout("access_token", "refresh_token", mock_response, mock_request, mock_db)

            mock_blacklist.assert_called_once_with(
                access_token="access_token", refresh_token="refresh_token", db=mock_db
            )
            # clear_refresh_cookies mirrors set_refresh_cookie attrs so __Host- deletions are honored
            mock_response.delete_cookie.assert_called_once_with(
                key="refresh_token", secure=False, samesite="strict", httponly=True
            )
            assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_with_invalid_jwt_raises_error(self, auth_service, mock_db, mock_response, mock_request):
        """Test that logout with JWT decode error raises UnauthorizedException."""
        from src.app.modules._base.exceptions import UnauthorizedError

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock) as mock_blacklist:
            mock_blacklist.side_effect = JWTError("Invalid token")

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.logout("invalid_token", "refresh_token", mock_response, mock_request, mock_db)

            assert "Invalid token" in str(exc_info.value.detail)


class TestAuthServiceEmailFlows:
    """Tests for email-related flows (password reset, verification)."""

    @pytest.mark.asyncio
    async def test_request_password_reset_sends_email(self, auth_service, mock_db):
        """Test that requesting password reset triggers email event."""
        mock_user = {
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "tenant_id": uuid4(),
            "is_active": True,
        }

        with (
            patch("src.app.modules.auth.services.passwords.UserService") as mock_get_service,
            patch("src.app.core.events.notifications.event_bus.publish", new_callable=AsyncMock) as mock_publish,
            patch("src.app.modules.auth.services.passwords.PasswordResetToken") as mock_token_model,
            patch("src.app.modules.auth.services.passwords.settings") as mock_settings,
        ):
            mock_user_service = MagicMock()
            mock_user_service.crud = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service
            mock_settings.app.FRONTEND_URL = "http://test-frontend"
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
            mock_user_service.crud.get.assert_called_once_with(
                db=mock_db, email="test@example.com", is_deleted=False, is_active=True
            )

            # Check token saved
            mock_db.add.assert_called()
            mock_db.commit.assert_called()

            # Check email notification sent via event bus
            mock_publish.assert_awaited_once()
            call_args = mock_publish.call_args[0]
            assert call_args[0] == "notification.request"
            assert call_args[1]["template_name"] == "password_reset"
            assert call_args[1]["to_email"] == "test@example.com"
            assert "reset_link" in call_args[1]["context"]
            assert "http://test-frontend" in call_args[1]["context"]["reset_link"]

    @pytest.mark.asyncio
    async def test_send_verification_email_creates_token_and_event(self, auth_service, mock_db):
        """Test sending verification email creates token and calls notification service."""
        mock_user = {
            "id": 1,
            "email": "test@example.com",
            "username": "testuser",
            "is_email_verified": False,
            "is_active": True,
            "tenant_id": uuid4(),
        }

        with (
            patch("src.app.modules.auth.services.email_verification.UserService") as mock_get_service,
            patch("src.app.modules.auth.services.email_verification.EmailVerificationToken") as mock_token_cls,
            patch("src.app.modules.auth.services.email_verification.settings") as mock_settings,
            patch("src.app.core.events.notifications.event_bus.publish", new_callable=AsyncMock) as mock_publish,
        ):
            mock_user_service = MagicMock()
            mock_user_service.crud = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service

            mock_settings.FRONTEND_URL = "http://test-frontend"

            mock_token_instance = MagicMock()
            mock_token_instance.token = "verify_token_123"
            mock_token_cls.return_value = mock_token_instance

            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()

            await auth_service.send_verification_email("test@example.com", mock_db)

            # Check token created and stored
            mock_token_cls.assert_called()
            assert mock_token_cls.call_args[1]["user_id"] == 1
            assert "token" in mock_token_cls.call_args[1]

            mock_db.add.assert_called_with(mock_token_instance)
            mock_db.commit.assert_called()

            # Check notification request was published to event bus
            mock_publish.assert_awaited_once()
            call_args = mock_publish.call_args[0]
            assert call_args[0] == "notification.request"
            assert call_args[1]["template_name"] == "email_verification"
            assert call_args[1]["to_email"] == "test@example.com"
            assert "verification_link" in call_args[1]["context"]

    @pytest.mark.asyncio
    async def test_verify_email_validates_and_updates_user(self, auth_service, mock_db):
        """Test verification logic updates user and deletes token."""
        mock_user = {"id": 99, "is_email_verified": False, "is_active": True}

        with (
            patch("src.app.modules.auth.services.email_verification.UserService") as mock_get_service,
        ):
            mock_user_service = MagicMock()
            mock_user_service.crud = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_user_service.crud.update = AsyncMock()
            mock_user_service._invalidate_user_cache = AsyncMock()
            mock_get_service.return_value = mock_user_service

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
            mock_user_service.crud.get.assert_called_once()
            mock_user_service.crud.update.assert_called_once()
            mock_db.delete.assert_called_once_with(mock_token)
            mock_db.commit.assert_called()
            assert result == {"message": "Email verified successfully."}

    @pytest.mark.asyncio
    async def test_verify_email_invalid_token_raises_error(self, auth_service, mock_db):
        """Test verify_email raises error if token is invalid or missing."""
        from src.app.modules._base.exceptions import UnauthorizedError

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(UnauthorizedError) as exc_info:
            await auth_service.verify_email("invalid_token", mock_db)

        assert "Invalid verification token" in str(exc_info.value.detail)


class TestAuthServiceSecurityBugFixes:
    """Tests for A1-A3 auth bug fixes (PHASE-AUTH-BUGS).

    A1: Token refresh for deleted users (SECURITY CRITICAL)
    A2: Missing is_email_verified in cached auth info
    A3: AttributeError in update_tier with dict response
    """

    @pytest.mark.asyncio
    async def test_a1_token_refresh_blocks_deleted_users(self, auth_service, mock_db, mock_response, mock_tenant):
        """A1 SECURITY: Token refresh must reject deleted users."""
        from src.app.modules._base.exceptions import UnauthorizedError

        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "deleteduser"
        mock_token_data.absolute_exp = None  # Bypass session expiration check

        with (
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.UserService") as mock_get_user_service,
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
        ):
            mock_verify.return_value = mock_token_data

            # Simulate deleted user in cache
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(
                return_value={
                    "id": 1,
                    "username": "deleteduser",
                    "is_deleted": True,  # User was deleted
                    "is_active": False,
                    "tier_id": 1,
                    "tier": {"name": "basic"},
                }
            )
            mock_get_user_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert "no longer active" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_a1_token_refresh_blocks_nonexistent_users(self, auth_service, mock_db, mock_response, mock_tenant):
        """A1 SECURITY: Token refresh must reject users that no longer exist."""
        from src.app.modules._base.exceptions import UnauthorizedError

        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "ghostuser"
        mock_token_data.absolute_exp = None  # Bypass session expiration check

        with (
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.UserService") as mock_get_user_service,
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
        ):
            mock_verify.return_value = mock_token_data

            # User not found (returns None)
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(return_value=None)
            mock_get_user_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedError) as exc_info:
                await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert "no longer active" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_a1_token_refresh_allows_active_users(self, auth_service, mock_db, mock_response, mock_tenant):
        """A1: Token refresh should work for active (non-deleted) users."""
        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "activeuser"
        mock_token_data.absolute_exp = None  # Bypass session expiration check
        mock_token_data.token_version = 0  # Match db_user token_version

        with (
            patch("src.app.modules.auth.service.settings") as mock_settings,
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch(
                "src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock
            ) as mock_create_access,
            patch(
                "src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock
            ) as mock_create_refresh,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.UserService") as mock_get_user_service,
            patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant),
            # S-4: session registration is orthogonal to refresh logic; it now
            # fails closed without Redis, so mock it out in this unit test.
            patch("src.app.core.security.login_helpers.register_login_session", new_callable=AsyncMock),
        ):
            from src.app.core.config import EnvironmentOption

            mock_settings.ENVIRONMENT = EnvironmentOption.LOCAL
            mock_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7
            mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
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
                    "is_active": True,
                    "tier_id": 1,
                    "tier": {"name": "premium"},
                    "token_version": 0,
                }
            )
            mock_get_user_service.return_value = mock_user_service

            result = await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert result["access_token"] == "new_access_token"
            assert result["token_type"] == "bearer"


class TestA2CachedAuthInfoFields:
    """Test suite for A2: is_email_verified and is_deleted in cached auth data.

    These fields must be present in cached user data to support:
    - Token refresh security checks (is_deleted)
    - Email verification status in JWT claims (is_email_verified)
    """

    @pytest.mark.asyncio
    async def test_a2_cached_auth_info_contains_required_fields(self):
        """A2: Cached auth info must include is_deleted and is_email_verified."""
        # Use fresh CRUDUser instance to avoid test pollution from module-level singleton
        from src.app.modules.users.crud import CRUDUser
        from src.app.modules.users.models import User

        crud = CRUDUser(User)

        # Mock the database session
        mock_db = AsyncMock()

        # Create a mock user ORM object with all required fields
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.name = "Test User"
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.hashed_password = "hashed"
        mock_user.profile_image_url = None
        mock_user.tier_id = 1
        mock_user.tenant_id = 1
        mock_user.is_superuser = False
        mock_user.is_active = True
        mock_user.is_deleted = False  # A2: Must be in result
        mock_user.is_email_verified = True  # A2: Must be in result
        mock_user.tier = MagicMock()
        mock_user.tier.name = "basic"

        # Mock the query result
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Call the method that should include A2 fields.
        # The tenant filter now lives in the tenant_scope seam; set the
        # context (the seam's actual read point) instead of patching the
        # old per-module get_current_tenant call site.
        import uuid as _uuid

        from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant

        set_current_tenant(TenantContext(tenant_id=_uuid.uuid4(), tenant_slug="t", tenant_name="T", is_active=True))
        try:
            result = await crud.get_user_with_tier_eager(db=mock_db, username="testuser")
        finally:
            clear_tenant_context()

        # A2 Assertions: These fields MUST be present
        assert result is not None, "Result should not be None"
        assert "is_deleted" in result, "A2 FIX: is_deleted must be in cached auth info"
        assert "is_email_verified" in result, "A2 FIX: is_email_verified must be in cached auth info"
        assert result["is_deleted"] is False
        assert result["is_email_verified"] is True

    @pytest.mark.asyncio
    async def test_a2_deleted_user_flag_propagates_correctly(self):
        """A2: is_deleted=True must propagate to cached data for security checks."""
        # Use fresh CRUDUser instance to avoid test pollution from module-level singleton
        from src.app.modules.users.crud import CRUDUser
        from src.app.modules.users.models import User

        crud = CRUDUser(User)

        mock_db = AsyncMock()

        # Create deleted user
        mock_user = MagicMock()
        mock_user.id = 99
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.name = "Deleted User"
        mock_user.username = "deleteduser"
        mock_user.email = "deleted@example.com"
        mock_user.hashed_password = "hashed"
        mock_user.profile_image_url = None
        mock_user.tier_id = 1
        mock_user.tenant_id = 1
        mock_user.is_superuser = False
        mock_user.is_active = False
        mock_user.is_deleted = True  # User is deleted
        mock_user.is_email_verified = False
        mock_user.tier = MagicMock()
        mock_user.tier.name = "basic"

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute = AsyncMock(return_value=mock_result)
        # Same seam fix: set the tenant context, not the old patch target.
        import uuid as _uuid

        from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant

        set_current_tenant(TenantContext(tenant_id=_uuid.uuid4(), tenant_slug="t", tenant_name="T", is_active=True))
        try:
            result = await crud.get_user_with_tier_eager(db=mock_db, email="deleted@example.com")
        finally:
            clear_tenant_context()
        assert result["is_deleted"] is True, "A2 SECURITY: is_deleted=True must propagate"
