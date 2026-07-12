"""Comprehensive tests for auth service - covering missing lines from coverage report.

This test file covers:
- Error handling blocks (lines 140-149, 478-479, 502-503)
- Edge cases (lines 211-212, 228-229, 240-255)
- Password reset/verification (lines 681, 718-769, 825, 862, 867, 872)
- Session invalidation (lines 615-619)
- MFA flow (lines 240-255)
- Token refresh edge cases (lines 351-355, 367)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response
from jwt import ExpiredSignatureError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.auth.service import AuthService

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def auth_service():
    """Create AuthService instance."""
    return AuthService()


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_request():
    """Mock HTTP request."""
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers.get.return_value = "TestClient/1.0"
    return request


@pytest.fixture
def mock_response():
    """Mock HTTP response."""
    return MagicMock(spec=Response)


# ============================================================================
# TEST: get_auth_service factory (line 50)
# ============================================================================


class TestGetAuthService:
    """Tests for get_auth_service factory function."""

    def test_get_auth_service_returns_auth_service_instance(self):
        """Test that factory returns AuthService instance."""
        from src.app.modules.auth.service import get_auth_service

        result = get_auth_service()
        assert isinstance(result, AuthService)


# ============================================================================
# TEST: authenticate_user edge cases (lines 89-92)
# ============================================================================


class TestAuthenticateUser:
    """Tests for authenticate_user method edge cases."""

    @pytest.mark.asyncio
    async def test_authenticate_user_with_email(self, auth_service, mock_db):
        """Test authenticate_user with email format (line 90)."""
        mock_user_service = MagicMock()
        mock_user_service.get_user_auth_info = AsyncMock(
            return_value={
                "id": 1,
                "username": "testuser",
                "email": "test@example.com",
                "hashed_password": "$2b$12$validhash",
                "is_active": True,
            }
        )

        with patch("src.app.modules.auth.service.get_service", return_value=mock_user_service):
            with patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = True
                result = await auth_service.authenticate_user("test@example.com", "password", mock_db)

                assert result is not None
                mock_user_service.get_user_auth_info.assert_called_once_with(email="test@example.com")

    @pytest.mark.asyncio
    async def test_authenticate_user_not_found_uses_dummy_hash(self, auth_service, mock_db):
        """Test that non-existent user uses dummy hash for timing attack prevention."""
        mock_user_service = MagicMock()
        mock_user_service.get_user_auth_info = AsyncMock(return_value=None)

        with patch("src.app.modules.auth.service.get_service", return_value=mock_user_service):
            with patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock) as mock_verify:
                result = await auth_service.authenticate_user("nonexistent@test.com", "wrongpassword", mock_db)

                assert result is None
                # Verify dummy hash was used for constant-time authentication
                mock_verify.assert_called_once()
                call_args = mock_verify.call_args[0]
                assert auth_service._DUMMY_BCRYPT_HASH in str(call_args)


# ============================================================================
# TEST: _publish_login_audit error handling (lines 140-149)
# ============================================================================


class TestPublishLoginAudit:
    """Tests for _publish_login_audit error handling."""

    @pytest.mark.asyncio
    async def test_publish_audit_event_bus_failure(self, auth_service, mock_request):
        """Test audit log failure when event_bus.publish fails (lines 140-149)."""
        with patch("src.app.modules.auth.service.event_bus.publish", side_effect=Exception("Redis connection failed")):
            with patch("src.app.modules.auth.service.get_tenant_context", return_value=None):
                with patch("src.app.modules.auth.service.settings") as mock_settings:
                    # Test fail_open mode (default)
                    mock_settings.AUDIT_LOG_FAILURE_MODE = "fail_open"
                    # Should not raise, just log
                    await auth_service._publish_login_audit(mock_request, "testuser", False, reason="test")

    @pytest.mark.asyncio
    async def test_publish_audit_fail_closed_mode(self, auth_service, mock_request):
        """Test AUDIT_LOG_FAILURE_MODE='fail_closed' raises 503."""
        from src.app.modules._base.exceptions import ServiceUnavailableError

        with patch("src.app.modules.auth.service.event_bus.publish", side_effect=Exception("Connection failed")):
            with patch("src.app.modules.auth.service.get_tenant_context", return_value=None):
                with patch("src.app.modules.auth.service.settings") as mock_settings:
                    mock_settings.auth.AUDIT_LOG_FAILURE_MODE = "fail_closed"

                    with pytest.raises(ServiceUnavailableError) as exc_info:
                        await auth_service._publish_login_audit(mock_request, "testuser", False, reason="test")

                    assert "Service temporarily unavailable" in str(exc_info.value)


# ============================================================================
# TEST: login method edge cases (lines 211-212, 228-229, 240-255)
# ============================================================================


class TestLogin:
    """Tests for login method edge cases."""

    @pytest.mark.asyncio
    async def test_login_redis_result_coercion(self, auth_service, mock_db, mock_redis, mock_request, mock_response):
        """Test Redis result coercion from bytes to int (lines 211-212)."""
        # Test with bytes result (what real Redis might return)
        mock_redis.eval = AsyncMock(return_value=b"5")

        with patch("src.app.modules.auth.service.settings") as mock_settings:
            mock_settings.AUTH_MAX_LOGIN_ATTEMPTS = 5
            mock_settings.AUTH_LOCKOUT_DURATION_SECONDS = 900
            mock_settings.ENVIRONMENT = "development"

            with patch.object(auth_service, "authenticate_user", new_callable=AsyncMock) as mock_auth:
                mock_auth.return_value = None  # Invalid credentials - tests the coercion path

                with patch.object(auth_service, "_publish_login_audit", new_callable=AsyncMock):
                    mock_mfa_svc = MagicMock()
                    mock_mfa_svc.is_mfa_enabled = AsyncMock(return_value=False)
                    with patch("src.app.modules.mfa.service.get_mfa_service", return_value=mock_mfa_svc):
                        # Should handle the bytes coercion and proceed
                        from fastapi import HTTPException

                        with pytest.raises(HTTPException) as exc_info:
                            await auth_service.login(
                                "testuser", "wrongpass", mock_response, mock_request, mock_db, mock_redis
                            )
                        assert exc_info.value.status_code == 401
                        assert "Wrong username, email or password." in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, auth_service, mock_db, mock_redis, mock_request, mock_response):
        """Test login with inactive user account (lines 228-229)."""
        mock_redis.eval = AsyncMock(return_value=0)
        mock_redis.delete = AsyncMock()

        mock_user = {
            "id": 1,
            "username": "inactiveuser",
            "email": "inactive@test.com",
            "hashed_password": "$2b$12$validhash",
            "is_active": False,  # Inactive user
            "tenant_id": "test-tenant",
        }

        with patch.object(auth_service, "authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = mock_user

            with patch.object(auth_service, "_publish_login_audit", new_callable=AsyncMock):
                mock_mfa_svc = MagicMock()
                mock_mfa_svc.is_mfa_enabled = AsyncMock(return_value=False)
                with patch("src.app.modules.mfa.service.get_mfa_service", return_value=mock_mfa_svc):
                    with pytest.raises(Exception) as exc_info:
                        await auth_service.login(
                            "inactiveuser", "password", mock_response, mock_request, mock_db, mock_redis
                        )

                    assert "no longer active" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_login_mfa_enabled_returns_mfa_token(
        self, auth_service, mock_db, mock_redis, mock_request, mock_response
    ):
        """Test login when MFA is enabled returns MFA token (lines 240-255)."""
        mock_redis.eval = AsyncMock(return_value=0)
        mock_redis.delete = AsyncMock()

        mock_user = {
            "id": 1,
            "username": "mfauser",
            "email": "mfa@test.com",
            "hashed_password": "$2b$12$validhash",
            "is_active": True,
            "tenant_id": "test-tenant",
            "token_version": 0,
        }

        with patch.object(auth_service, "authenticate_user", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = mock_user

            with patch.object(auth_service, "_publish_login_audit", new_callable=AsyncMock):
                mock_mfa_instance = MagicMock()
                mock_mfa_instance.is_mfa_enabled = AsyncMock(return_value=True)
                with patch("src.app.modules.mfa.service.get_mfa_service", return_value=mock_mfa_instance):
                    with patch(
                        "src.app.modules.auth.service.get_mfa_pending_token_manager"
                    ) as mock_token_manager_factory:
                        mock_manager_instance = MagicMock()
                        mock_token_manager_factory.return_value = mock_manager_instance
                        mock_manager_instance.create = AsyncMock()

                        with patch("src.app.core.security.tokens.TokenGenerator.generate_magic_link_token") as mock_gen:
                            mock_gen.return_value = "mfa_token_123"
                            result = await auth_service.login(
                                "mfauser", "password", mock_response, mock_request, mock_db, mock_redis
                            )

                            assert result["mfa_required"] is True
                            assert result["mfa_token"] == "mfa_token_123"


# ============================================================================
# TEST: refresh_access_token edge cases (lines 351-355, 367)
# ============================================================================


class TestRefreshAccessToken:
    """Tests for refresh_access_token edge cases."""

    @pytest.mark.asyncio
    async def test_refresh_token_missing(self, auth_service, mock_db, mock_response):
        """Test refresh with missing token (line 340-341)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.refresh_access_token(None, mock_response, mock_db)

        assert "missing" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, auth_service, mock_db, mock_response):
        """Test refresh with invalid token."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = None

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("invalid_token", mock_response, mock_db)

            assert "invalid" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_refresh_absolute_session_expired(self, auth_service, mock_db, mock_response):
        """Test refresh when absolute session has expired (lines 351-355)."""
        from datetime import UTC, datetime, timedelta

        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_user_data = MagicMock()
        mock_user_data.username_or_email = "testuser"
        mock_user_data.absolute_exp = (datetime.now(UTC) - timedelta(hours=1)).timestamp()

        with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = mock_user_data

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("expired_session_token", mock_response, mock_db)

            assert "session expired" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_refresh_with_email_subject(self, auth_service, mock_db, mock_response):
        """Test refresh with email-based subject (line 367)."""
        mock_user_data = MagicMock()
        mock_user_data.username_or_email = "test@example.com"
        mock_user_data.absolute_exp = None
        mock_user_data.token_version = 0  # Match db_user token_version

        mock_db_user = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "is_active": True,
            "is_deleted": False,
            "tier": {"name": "free"},
            "tier_id": 1,
            "tenant_id": "test-tenant",
            "token_version": 0,
        }

        mock_user_service = MagicMock()
        mock_user_service.get_user_auth_info = AsyncMock(return_value=mock_db_user)

        with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = mock_user_data

            with patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock):
                with patch("src.app.modules.auth.service.get_service", return_value=mock_user_service):
                    with patch(
                        "src.app.modules.auth.service.create_access_token", new_callable=AsyncMock
                    ) as mock_create:
                        with patch("src.app.modules.auth.service.create_refresh_token", new_callable=AsyncMock):
                            mock_create.return_value = "new_access_token"

                            mock_response.set_cookie = MagicMock()

                            # Should call with email parameter
                            result = await auth_service.refresh_access_token(
                                "valid_refresh_token", mock_response, mock_db
                            )

                            assert result["access_token"] == "new_access_token"


# ============================================================================
# TEST: logout error handling (lines 455-456, 466, 478-479, 502-503)
# ============================================================================


class TestLogout:
    """Tests for logout error handling."""

    @pytest.mark.asyncio
    async def test_logout_refresh_token_missing(self, auth_service, mock_db, mock_request, mock_response):
        """Test logout with missing refresh token (line 448-449)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException) as exc_info:
            await auth_service.logout(
                access_token="access_token",
                refresh_token=None,
                response=mock_response,
                request=mock_request,
                db=mock_db,
            )

        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_logout_expired_access_token(self, auth_service, mock_db, mock_request, mock_response):
        """Test logout with expired access token still proceeds (lines 455-456)."""
        mock_response.delete_cookie = MagicMock()

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock):
            with patch("src.app.modules.auth.service.verify_token", side_effect=ExpiredSignatureError()):
                # Should not raise - logout proceeds even with expired access token
                result = await auth_service.logout(
                    access_token="expired_token",
                    refresh_token="valid_refresh",
                    response=mock_response,
                    request=mock_request,
                    db=mock_db,
                )

                assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_user_not_found_continues(self, auth_service, mock_db, mock_request, mock_response):
        """Test logout when user lookup fails still proceeds (line 466)."""
        mock_response.delete_cookie = MagicMock()

        mock_user_data = MagicMock()
        mock_user_data.username_or_email = "testuser"

        mock_user_service = MagicMock()
        mock_user_service.get_user_auth_info = AsyncMock(return_value=None)

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock):
            with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = mock_user_data

                with patch("src.app.modules.auth.service.get_service", return_value=mock_user_service):
                    # Should not raise even if user not found
                    result = await auth_service.logout(
                        access_token="access_token",
                        refresh_token="refresh_token",
                        response=mock_response,
                        request=mock_request,
                        db=mock_db,
                    )

                    assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_session_invalidation_error(self, auth_service, mock_db, mock_request, mock_response):
        """Test logout continues when session invalidation fails (lines 478-479)."""
        mock_response.delete_cookie = MagicMock()

        mock_user_data = MagicMock()
        mock_user_data.username_or_email = "testuser"

        mock_db_user = {
            "id": 1,
            "username": "testuser",
        }

        mock_user_service = MagicMock()
        mock_user_service.get_user_auth_info = AsyncMock(return_value=mock_db_user)

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock):
            with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = mock_user_data

                with patch("src.app.modules.auth.service.get_service", return_value=mock_user_service):
                    with patch("src.app.core.security.session_manager.session_manager") as mock_session_mgr:
                        mock_session_mgr.invalidate_session = AsyncMock(side_effect=Exception("Redis error"))

                        # Should not raise - just log warning
                        result = await auth_service.logout(
                            access_token="access_token",
                            refresh_token="refresh_token",
                            response=mock_response,
                            request=mock_request,
                            db=mock_db,
                        )

                        assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_logout_audit_failure(self, auth_service, mock_db, mock_request, mock_response):
        """Test logout continues when audit log fails (lines 502-503)."""
        mock_response.delete_cookie = MagicMock()

        mock_user_data = MagicMock()
        mock_user_data.username_or_email = "testuser"

        with patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock):
            with patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = mock_user_data

                with patch("src.app.modules.auth.service.event_bus.publish", side_effect=Exception("Audit failed")):
                    # Should not raise even if audit fails
                    result = await auth_service.logout(
                        access_token="access_token",
                        refresh_token="refresh_token",
                        response=mock_response,
                        request=mock_request,
                        db=mock_db,
                    )

                    assert result["message"] == "Logged out successfully"


# ============================================================================
# TEST: change_password error handling (lines 547-602)
# ============================================================================


class TestChangePassword:
    """Tests for change_password edge cases."""

    @pytest.mark.asyncio
    async def test_change_password_user_not_found(self, auth_service, mock_db):
        """Test change password when user not found (lines 547-549)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_db.get = AsyncMock(return_value=None)

        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.change_password(
                    user_id=999,
                    current_password="oldpassword",
                    new_password="NewPassword123!",
                    db=mock_db,
                )

            assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_change_password_inactive_user(self, auth_service, mock_db):
        """Test change password with inactive user."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_user = {
            "id": 1,
            "username": "inactiveuser",
            "hashed_password": "$2b$12$validhash",
            "is_active": False,
        }

        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service

            with pytest.raises(UnauthorizedException):
                await auth_service.change_password(
                    user_id=1,
                    current_password="oldpassword",
                    new_password="NewPassword123!",
                    db=mock_db,
                )

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, auth_service, mock_db):
        """Test change password with incorrect current password."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_user = {
            "id": 1,
            "username": "testuser",
            "hashed_password": "$2b$12$validhash",
            "is_active": True,
            "tenant_id": "test-tenant",
        }

        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = False  # Wrong password

                with pytest.raises(UnauthorizedException) as exc_info:
                    await auth_service.change_password(
                        user_id=1,
                        current_password="wrongpassword",
                        new_password="NewPassword123!",
                        db=mock_db,
                    )

                assert "incorrect" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_change_password_complexity_failure(self, auth_service, mock_db):
        """Test change password with insufficient complexity."""
        from src.app.core.exceptions.http_exceptions import BadRequestException

        mock_user = {
            "id": 1,
            "username": "testuser",
            "hashed_password": "$2b$12$validhash",
            "is_active": True,
            "tenant_id": "test-tenant",
        }

        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock) as mock_verify:
                mock_verify.return_value = True

                with pytest.raises(BadRequestException):
                    await auth_service.change_password(
                        user_id=1,
                        current_password="oldpassword",
                        new_password="weak",  # Too simple
                        db=mock_db,
                    )


# ============================================================================
# TEST: _invalidate_all_user_sessions (lines 615-619)
# ============================================================================


class TestInvalidateAllUserSessions:
    """Tests for _invalidate_all_user_sessions method."""

    @pytest.mark.asyncio
    async def test_invalidate_all_user_sessions_increments_version(self, auth_service, mock_db):
        """Test that session invalidation increments token_version (lines 615-619)."""
        mock_user = {
            "id": 1,
            "username": "testuser",
        }

        mock_db.execute = AsyncMock()

        await auth_service._invalidate_all_user_sessions(mock_user, mock_db)

        # Verify update was called
        mock_db.execute.assert_called_once()


# ============================================================================
# TEST: request_password_reset edge cases (line 681)
# ============================================================================


class TestRequestPasswordReset:
    """Tests for request_password_reset edge cases."""

    @pytest.mark.asyncio
    async def test_request_password_reset_user_not_found(self, auth_service, mock_db):
        """Test password reset request for non-existent user - still returns success."""
        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await auth_service.request_password_reset(
                    email="nonexistent@test.com",
                    db=mock_db,
                )

                assert "sent" in result["message"].lower()
                # Verify timing equalization was applied (with jitter)
                assert mock_sleep.called
                sleep_time = mock_sleep.call_args[0][0]
                assert 0.1 <= sleep_time <= 0.2

    @pytest.mark.asyncio
    async def test_request_password_reset_inactive_user(self, auth_service, mock_db):
        """Test password reset for inactive user - returns success for security."""
        # Note: Due to is_active=True filter in crud_users.get, inactive users
        # won't be found, so this tests the security timing equalization path
        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await auth_service.request_password_reset(
                    email="inactive@test.com",
                    db=mock_db,
                )

                assert "sent" in result["message"].lower()
                # Should equalize timing for security (with jitter)
                assert mock_sleep.called
                sleep_time = mock_sleep.call_args[0][0]
                assert 0.1 <= sleep_time <= 0.2


# ============================================================================
# TEST: reset_password edge cases (lines 718-769)
# ============================================================================


class TestResetPassword:
    """Tests for reset_password edge cases."""

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self, auth_service, mock_db):
        """Test reset with invalid token (lines 721-722)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                with patch("src.app.modules.auth.service.get_service"):
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(UnauthorizedException) as exc_info:
                        await auth_service.reset_password(
                            token="invalid_token",
                            new_password="NewPassword123!",
                            db=mock_db,
                        )

                    assert "invalid" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_reset_password_token_already_used(self, auth_service, mock_db):
        """Test reset with already-used token (lines 725-726)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_valid = False
        mock_token.used = True
        mock_token.is_expired = False

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                mock_db.execute = AsyncMock(return_value=mock_result)

                with pytest.raises(UnauthorizedException) as exc_info:
                    await auth_service.reset_password(
                        token="used_token",
                        new_password="NewPassword123!",
                        db=mock_db,
                    )

                assert "already been used" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_reset_password_token_expired(self, auth_service, mock_db):
        """Test reset with expired token (lines 727-728)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_valid = False
        mock_token.used = False
        mock_token.is_expired = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                mock_db.execute = AsyncMock(return_value=mock_result)

                with pytest.raises(UnauthorizedException) as exc_info:
                    await auth_service.reset_password(
                        token="expired_token",
                        new_password="NewPassword123!",
                        db=mock_db,
                    )

                assert "expired" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_reset_password_deleted_user(self, auth_service, mock_db):
        """Test reset for deleted user account (lines 738-739)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_valid = True
        mock_token.user_id = 1
        mock_token.used = False
        mock_token.is_expired = False

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        mock_user = {
            "id": 1,
            "is_deleted": False,
            "is_active": False,
            "is_email_verified": True,
        }

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                with patch("src.app.modules.auth.service.get_service") as mock_get_service:
                    mock_user_service = MagicMock()
                    mock_user_service.crud.get = AsyncMock(return_value=mock_user)
                    mock_get_service.return_value = mock_user_service
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(UnauthorizedException) as exc_info:
                        await auth_service.reset_password(
                            token="valid_token",
                            new_password="NewPassword123!",
                            db=mock_db,
                        )

                    assert (
                        "not accessible" in str(exc_info.value.detail).lower()
                        or "support" in str(exc_info.value.detail).lower()
                    )

    @pytest.mark.asyncio
    async def test_reset_password_inactive_user(self, auth_service, mock_db):
        """Test reset for inactive user account."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_valid = True
        mock_token.user_id = 1
        mock_token.used = False
        mock_token.is_expired = False

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        mock_user = {
            "id": 1,
            "is_deleted": False,
            "is_active": False,
        }

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                with patch("src.app.modules.auth.service.get_service") as mock_get_service:
                    mock_user_service = MagicMock()
                    mock_user_service.crud = MagicMock()
                    mock_user_service.crud.get = AsyncMock(return_value=mock_user)
                    mock_get_service.return_value = mock_user_service
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(UnauthorizedException):
                        await auth_service.reset_password(
                            token="valid_token",
                            new_password="NewPassword123!",
                            db=mock_db,
                        )

    @pytest.mark.asyncio
    async def test_reset_password_complexity_failure(self, auth_service, mock_db):
        """Test reset with insufficient password complexity."""
        from src.app.core.exceptions.http_exceptions import BadRequestException

        mock_token = MagicMock()
        mock_token.is_valid = True
        mock_token.user_id = 1
        mock_token.used = False
        mock_token.is_expired = False

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        mock_user = {
            "id": 1,
            "is_deleted": False,
            "is_active": True,
            "tenant_id": "test-tenant",
        }

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.PasswordResetToken"):
                with patch("src.app.modules.auth.service.get_service") as mock_get_service:
                    mock_user_service = MagicMock()
                    mock_user_service.crud = MagicMock()
                    mock_user_service.crud.get = AsyncMock(return_value=mock_user)
                    mock_get_service.return_value = mock_user_service
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(BadRequestException):
                        await auth_service.reset_password(
                            token="valid_token",
                            new_password="weak",
                            db=mock_db,
                        )


# ============================================================================
# TEST: send_verification_email edge cases (line 825)
# ============================================================================


class TestSendVerificationEmail:
    """Tests for send_verification_email edge cases."""

    @pytest.mark.asyncio
    async def test_send_verification_email_user_not_found_or_verified(self, auth_service, mock_db):
        """Test verification email for non-existent or already verified user (line 825)."""
        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await auth_service.send_verification_email(
                    email="nonexistent@test.com",
                    db=mock_db,
                )

                assert "sent" in result["message"].lower()
                # Verify timing equalization (with jitter)
                assert mock_sleep.called
                sleep_time = mock_sleep.call_args[0][0]
                assert 0.1 <= sleep_time <= 0.2

    @pytest.mark.asyncio
    async def test_send_verification_email_already_verified(self, auth_service, mock_db):
        """Test verification email when email already verified."""
        mock_user = {
            "id": 1,
            "email": "verified@test.com",
            "is_email_verified": True,
            "is_deleted": False,
            "is_active": True,
        }

        with patch("src.app.modules.auth.service.get_service") as mock_get_service:
            mock_user_service = MagicMock()
            mock_user_service.crud.get = AsyncMock(return_value=mock_user)
            mock_get_service.return_value = mock_user_service

            with patch("src.app.modules.auth.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await auth_service.send_verification_email(
                    email="verified@test.com",
                    db=mock_db,
                )

                assert "sent" in result["message"].lower()
                # Verify timing equalization (with jitter)
                assert mock_sleep.called
                sleep_time = mock_sleep.call_args[0][0]
                assert 0.1 <= sleep_time <= 0.2


# ============================================================================
# TEST: verify_email edge cases (lines 862, 867, 872)
# ============================================================================


class TestVerifyEmail:
    """Tests for verify_email edge cases."""

    @pytest.mark.asyncio
    async def test_verify_email_invalid_token(self, auth_service, mock_db):
        """Test verification with invalid token (lines 858-859)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.EmailVerificationToken"):
                mock_db.execute = AsyncMock(return_value=mock_result)

                with pytest.raises(UnauthorizedException) as exc_info:
                    await auth_service.verify_email(
                        token="invalid_token",
                        db=mock_db,
                    )

                assert "invalid" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_email_expired_token(self, auth_service, mock_db):
        """Test verification with expired token (lines 861-862)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_expired = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.EmailVerificationToken"):
                mock_db.execute = AsyncMock(return_value=mock_result)

                with pytest.raises(UnauthorizedException) as exc_info:
                    await auth_service.verify_email(
                        token="expired_token",
                        db=mock_db,
                    )

                assert "expired" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_email_user_not_found(self, auth_service, mock_db):
        """Test verification when user no longer exists (lines 865-867)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_expired = False
        mock_token.user_id = 999

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.EmailVerificationToken"):
                with patch("src.app.modules.auth.service.get_service") as mock_get_service:
                    mock_user_service = MagicMock()
                    mock_user_service.crud.get = AsyncMock(return_value=None)
                    mock_get_service.return_value = mock_user_service
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(UnauthorizedException) as exc_info:
                        await auth_service.verify_email(
                            token="valid_token",
                            db=mock_db,
                        )

                    assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_verify_email_inactive_user(self, auth_service, mock_db):
        """Test verification for inactive user account (lines 871-872)."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        mock_token = MagicMock()
        mock_token.is_expired = False
        mock_token.user_id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token

        mock_user = {
            "id": 1,
            "is_deleted": False,
            "is_active": False,
            "is_email_verified": True,
        }

        with patch("src.app.modules.auth.service.select"):
            with patch("src.app.modules.auth.service.EmailVerificationToken"):
                with patch("src.app.modules.auth.service.get_service") as mock_get_service:
                    mock_user_service = MagicMock()
                    mock_user_service.crud.get = AsyncMock(return_value=mock_user)
                    mock_user_service.crud.update = AsyncMock()
                    mock_user_service._invalidate_user_cache = AsyncMock()
                    mock_get_service.return_value = mock_user_service
                    mock_db.execute = AsyncMock(return_value=mock_result)

                    with pytest.raises(UnauthorizedException) as exc_info:
                        await auth_service.verify_email(
                            token="valid_token",
                            db=mock_db,
                        )

                    assert "inactive" in str(exc_info.value.detail).lower()
