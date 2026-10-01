"""P1-2: cookie-only logout must revoke the server-side session.

The SPA holds the access token in memory and the refresh token in an HttpOnly
cookie the frontend cannot read. At logout no access token is presented, so
``_revoke_session`` used to early-return ``(None, False)`` and the Redis
session entry survived logout. The refresh token carries the same ``sid``
claim, so it can reach the session registry.

Tokens here are REAL JWTs with distinct sid claims, so the assertions pin
WHICH token supplied the sid rather than accepting any non-empty string.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

ACCESS_SID = "access-session-id-0001"
REFRESH_SID = "refresh-session-id-0002"  # deliberately distinct from ACCESS_SID
UNSEEN_SID = "never-registered-session-9999"


def _token(sid: str, token_type: str = "access") -> str:
    """A structurally real JWT carrying a known sid claim."""
    return jwt.encode(
        {
            "sub": "7",
            "sid": sid,
            "type": token_type,
            "exp": int(time.time()) + 3600,
        },
        "test-signing-key",
        algorithm="HS256",
    )

@pytest.fixture
def auth_service():
    from src.app.modules.auth.service import AuthService

    return AuthService.__new__(AuthService)


def _user_data(username: str = "operator@example.com"):
    data = MagicMock()
    data.username_or_email = username
    return data


def _db_user(user_id: int = 7):
    return {"id": user_id, "tenant_id": 1, "username": "operator"}


def _request():
    """A request mock whose header/IP accessors return real strings.

    The audit payload is pydantic-validated, so MagicMock attribute access
    would fail validation for reasons unrelated to session revocation.
    """
    request = MagicMock()
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


def _invalidate_patch():
    return patch(
        "src.app.core.security.session_manager.session_manager.invalidate_session",
        new_callable=AsyncMock,
    )


class TestRevokeSessionFallback:
    """_revoke_session invalidates the session from a refresh token alone."""

    @pytest.mark.asyncio
    async def test_refresh_token_alone_revokes_session(self, auth_service) -> None:
        """No access token, user_data from refresh: the session IS invalidated."""
        with (
            patch.object(
                auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()
            ),
            _invalidate_patch() as invalidate,
        ):
            db_user, revoked = await auth_service._revoke_session(
                access_token=None,
                user_data=_user_data(),
                db=MagicMock(),
                refresh_token=_token(REFRESH_SID, "refresh"),
            )

        assert revoked is True
        assert db_user == _db_user()
        invalidate.assert_awaited_once()
        user_id_arg, session_id_arg = invalidate.await_args[0]
        assert user_id_arg == 7
        # The sid comes from the REFRESH token's claim, not a token prefix.
        assert session_id_arg == REFRESH_SID

    @pytest.mark.asyncio
    async def test_no_token_at_all_is_a_noop(self, auth_service) -> None:
        """Without any token there is no sid to invalidate — unchanged."""
        with _invalidate_patch() as invalidate:
            db_user, revoked = await auth_service._revoke_session(
                access_token=None, user_data=_user_data(), db=MagicMock(), refresh_token=None
            )

        assert (db_user, revoked) == (None, False)
        invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_user_data_still_skips(self, auth_service) -> None:
        """An unresolvable identity cannot address a session row."""
        with _invalidate_patch() as invalidate:
            db_user, revoked = await auth_service._revoke_session(
                access_token=None, user_data=None, db=MagicMock(), refresh_token="refresh.jwt.token"
            )

        assert (db_user, revoked) == (None, False)
        invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_access_token_path_unchanged(self, auth_service) -> None:
        """Regression lock: the header-based path still uses the access token."""
        with (
            patch.object(
                auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()
            ),
            _invalidate_patch() as invalidate,
        ):
            db_user, revoked = await auth_service._revoke_session(
                access_token=_token(ACCESS_SID),
                user_data=_user_data(),
                db=MagicMock(),
                refresh_token=_token(REFRESH_SID, "refresh"),
            )

        assert revoked is True
        assert invalidate.await_args[0][0] == 7
        assert invalidate.await_args[0][1] == ACCESS_SID

    @pytest.mark.asyncio
    async def test_unregistered_session_reports_not_revoked(self, auth_service) -> None:
        """A logout with no matching session entry must NOT claim revocation.

        ``invalidate_session`` returns False when the registry holds no such
        entry. Reporting True would tell the client a session was revoked when
        nothing was — the exact reason P1-2 added the field.
        """
        with (
            patch.object(
                auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()
            ),
            patch(
                "src.app.core.security.session_manager.session_manager.invalidate_session",
                new_callable=AsyncMock,
                return_value=False,
            ) as invalidate,
        ):
            db_user, revoked = await auth_service._revoke_session(
                access_token=None,
                user_data=_user_data(),
                db=MagicMock(),
                refresh_token=_token(UNSEEN_SID, "refresh"),
            )

        assert revoked is False
        assert db_user == _db_user()
        invalidate.assert_awaited_once()


class TestLogoutResolvesIdentityFromRefresh:
    """logout() derives user_data from the refresh cookie when needed."""

    @pytest.mark.asyncio
    async def test_cookie_only_logout_revokes_session(self, auth_service) -> None:
        """The normal browser case: no access token, session still revoked."""
        with (
            patch(
                "src.app.modules.auth.service.verify_token",
                new_callable=AsyncMock,
                return_value=_user_data(),
            ) as verify,
            patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock),
            patch.object(
                auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()
            ),
            _invalidate_patch() as invalidate,
            patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock),
        ):
            result = await auth_service.logout(
                access_token=None,
                refresh_token="refresh.jwt.token",
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert result["session_revoked"] is True
        assert result["message"] == "Logged out successfully"
        invalidate.assert_awaited_once()
        # Identity was resolved from the REFRESH token.
        verify.assert_awaited_once()
        assert verify.await_args[0][1].value == "refresh"

    @pytest.mark.asyncio
    async def test_no_token_still_raises(self, auth_service) -> None:
        """The 'No token provided' guard is unchanged."""
        from src.app.shared.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError, match="No token provided"):
            await auth_service.logout(
                access_token=None, refresh_token=None, response=MagicMock(), request=_request(), db=MagicMock()
            )

    @pytest.mark.asyncio
    async def test_header_logout_still_works(self, auth_service) -> None:
        """Regression lock: header-based logout is byte-for-byte the old path."""
        with (
            patch(
                "src.app.modules.auth.service.verify_token",
                new_callable=AsyncMock,
                return_value=_user_data(),
            ) as verify,
            patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock),
            patch.object(
                auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()
            ),
            _invalidate_patch(),
            patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock),
        ):
            result = await auth_service.logout(
                access_token="access.jwt.token",
                refresh_token=None,
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert result["session_revoked"] is True
        # Only the access token was verified — no refresh fallback needed.
        verify.assert_awaited_once()
        assert verify.await_args[0][1].value == "access"


class TestLogoutResponseSchema:
    """The client can tell a full logout from a partial one."""

    def test_session_revoked_is_exposed(self) -> None:
        from src.app.modules.auth.schemas import LogoutResponse

        assert LogoutResponse(message="Logged out successfully", session_revoked=True).session_revoked is True
        assert "session_revoked" in LogoutResponse.model_fields

    def test_extra_keys_are_still_filtered(self) -> None:
        """Unknown keys must not leak into the response body."""
        from src.app.modules.auth.schemas import LogoutResponse

        dumped = LogoutResponse.model_validate({"message": "ok", "session_revoked": True}).model_dump()
        assert set(dumped) == {"message", "session_revoked"}
