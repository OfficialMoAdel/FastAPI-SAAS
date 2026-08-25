"""Regression tests for A1/A2/A3 session-security fixes.

A2: refresh rotation must carry absolute_exp/iat forward; expired absolute
    sessions are rejected.
A3: stable sid claim is shared by access+refresh tokens and survives rotation;
    old sessions invalidate by sid (not the refresh token's jti).
A1: blacklist tri-state — None (Redis down) triggers DB fallback in deps/auth
    instead of being treated as "not blacklisted"; middleware persists the
    tri-state; circuit-breaker open honors TOKEN_CACHE_FAILURE_MODE.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.token_blacklist_cache import (
    _get_available_redis_client_for_blacklist,
    is_token_blacklisted,
)
from src.app.modules.auth.services.tokens import TokenIssuer


def claims(token: str) -> dict:
    """Decode JWT payload without verification (test helper)."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


# ---------------------------------------------------------------------------
# A3/A2: sid + absolute_exp claim lifecycle
# ---------------------------------------------------------------------------


class TestSessionClaimLifecycle:
    @pytest.mark.asyncio
    async def test_login_tokens_share_sid(self):
        access, refresh = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        assert claims(access)["sid"] == claims(refresh)["sid"]

    @pytest.mark.asyncio
    async def test_login_refresh_has_absolute_exp(self):
        _, refresh = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        assert "absolute_exp" in claims(refresh)

    @pytest.mark.asyncio
    async def test_jti_unique_per_token(self):
        access, refresh = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        assert claims(access)["jti"] != claims(refresh)["jti"]

    @pytest.mark.asyncio
    async def test_rotation_preserves_sid_absolute_exp_iat(self):
        _, old_refresh = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        old = claims(old_refresh)

        carried = {k: old[k] for k in ("sid", "absolute_exp", "iat")}
        new_access, new_refresh = await TokenIssuer.rotate_token_pair(
            {"sub": "u", "token_version": 0}, carried
        )
        new_r = claims(new_refresh)
        assert new_r["sid"] == old["sid"]
        assert new_r["absolute_exp"] == old["absolute_exp"]
        assert new_r["iat"] == old["iat"]
        # Rotation still mints a fresh jti for blacklisting the old token.
        assert new_r["jti"] != old["jti"]
        # Access token carries the same sid.
        assert claims(new_access)["sid"] == old["sid"]

    @pytest.mark.asyncio
    async def test_rotation_without_carried_claims_mints_fresh(self):
        """Sanity: brand-new pair (no carried claims) gets fresh values."""
        _, first = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        await asyncio.sleep(0.001)  # ensure wall clock advances between mints
        _, second = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        assert claims(first)["sid"] != claims(second)["sid"]
        assert claims(first)["absolute_exp"] != claims(second)["absolute_exp"]


class TestVerifyTokenAbsoluteExp:
    async def _verify(self, payload):
        from src.app.core.security.jwt_service import TokenType, verify_token

        db = MagicMock()
        redis_client = MagicMock()
        redis_client.get = AsyncMock(return_value=None)
        user = {"token_version": payload.get("token_version", 0)}
        with (
            patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
            patch(
                "src.app.core.security.jwt_service.is_token_blacklisted",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.core.utils.cache.client", redis_client),
            patch(
                "src.app.modules.users.crud.crud_users.get",
                new=AsyncMock(return_value=user),
            ),
        ):
            return await verify_token("tok", TokenType.ACCESS, db)

    @pytest.mark.asyncio
    async def test_expired_absolute_exp_rejected(self):
        result = await self._verify(
            {
                "sub": "u",
                "token_type": "access",
                "jti": "abc",
                "sid": "s1",
                "token_version": 0,
                "absolute_exp": (datetime.now(UTC) - timedelta(days=1)).timestamp(),
            }
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_sid_falls_back_to_jti(self):
        result = await self._verify(
            {"sub": "u", "token_type": "access", "jti": "abc", "token_version": 0}
        )
        assert result is not None
        assert result.sid == "abc"

    @pytest.mark.asyncio
    async def test_valid_absolute_exp_accepted_and_surfaces_sid_iat(self):
        iat = datetime.now(UTC).timestamp()
        result = await self._verify(
            {
                "sub": "u",
                "token_type": "access",
                "jti": "abc",
                "sid": "s1",
                "token_version": 0,
                "absolute_exp": (datetime.now(UTC) + timedelta(days=1)).timestamp(),
                "iat": iat,
            }
        )
        assert result is not None
        assert result.sid == "s1"
        assert result.iat == iat


class TestFreshMintSanity:
    @pytest.mark.asyncio
    async def test_rotation_without_carried_claims_mints_fresh_sid(self):
        """Sanity: brand-new pair (no carried claims) gets a fresh sid."""
        _, first = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        await asyncio.sleep(0.001)
        _, second = await TokenIssuer.create_token_pair({"sub": "u", "token_version": 0})
        assert claims(first)["sid"] != claims(second)["sid"]
        # Fresh mint stamps absolute_exp at now + MAX_SESSION_DURATION_DAYS.
        from src.app.core.config import settings

        expected = (
            datetime.now(UTC) + timedelta(days=settings.MAX_SESSION_DURATION_DAYS)
        ).timestamp()
        assert abs(claims(first)["absolute_exp"] - expected) < 5.0



class TestRefreshInvalidatesBySid:
    @pytest.mark.asyncio
    async def test_invalidate_session_called_with_old_sid(self):
        """A3: rotation invalidates the OLD session via its sid, not refresh jti."""
        from src.app.modules.auth.service import AuthService

        auth_service = AuthService()
        mock_db = AsyncMock()
        mock_response = MagicMock()

        old_sid = "session-sid-123"
        old_jti = "refresh-jti-456"

        token_data = MagicMock()
        token_data.username_or_email = "user"
        token_data.absolute_exp = None
        token_data.token_version = 0
        token_data.jti = old_jti
        token_data.sid = old_sid

        db_user = {
            "id": 1,
            "username": "user",
            "email": "user@example.com",
            "is_active": True,
            "is_deleted": False,
            "tenant_id": "t1",
            "token_version": 0,
        }

        with (
            patch(
                "src.app.modules.auth.service.verify_token",
                new_callable=AsyncMock,
                return_value=token_data,
            ),
            patch(
                "src.app.modules.users.service.UserService.get_user_auth_info",
                new_callable=AsyncMock,
                return_value=db_user,
            ),
            patch(
                "src.app.core.security.jwt_utils.decode_jwt_token",
                new_callable=AsyncMock,
                return_value={"sid": old_sid, "absolute_exp": 123.0, "iat": 456.0},
            ),
            patch(
                "src.app.modules.auth.service.TokenIssuer"
            ) as mock_issuer_cls,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch(
                "src.app.core.utils.cache.client", None
            ),  # skip refresh lock
            patch(
                "src.app.core.security.session_manager.session_manager"
            ) as mock_sm,
        ):
            mock_issuer_cls.rotate_token_pair = AsyncMock(
                return_value=("new_access", "new_refresh")
            )
            mock_sm.invalidate_session = AsyncMock(return_value=True)

            result = await auth_service.refresh_access_token(
                "old_refresh_token", mock_response, mock_db
            )

            assert result["access_token"] == "new_access"
            # The old session was invalidated under the STABLE sid...
            mock_sm.invalidate_session.assert_awaited_once_with(1, old_sid)
            # ...NOT under the refresh token's per-token jti.
            assert all(call.args[1] != old_jti for call in mock_sm.invalidate_session.await_args_list)


# ---------------------------------------------------------------------------
# A1: tri-state blacklist propagation
# ---------------------------------------------------------------------------


class TestTriStateBlacklist:
    @pytest.fixture
    def request_factory(self):
        def _make(state=None):
            request = MagicMock(spec=__import__("fastapi").Request)
            request.headers = {}
            request.app = MagicMock()
            request.app.dependency_overrides = {}
            request.state = state if state is not None else MagicMock()
            return request

        return _make

    @pytest.mark.asyncio
    async def test_state_none_triggers_db_fallback(self, request_factory):
        """Redis down -> middleware stores None -> deps run the DB fallback."""
        from src.app.api.deps.auth import get_auth_context

        state = MagicMock()
        state.is_token_blacklisted = None  # Redis was down during probe
        request = request_factory(state)
        payload = {"jti": "j1", "token_type": "access", "sub": "user@example.com", "token_version": 0}
        user = {
            "id": 1,
            "email": "user@example.com",
            "is_active": True,
            "is_deleted": False,
            "token_version": 0,
            "tenant_id": 5,
        }
        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
            patch(
                "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
                new=AsyncMock(return_value=True),
            ) as mock_fb,
            patch("src.app.api.deps.auth.UserService") as mock_us,
        ):
            svc = mock_us.return_value
            svc.get_user_auth_info = AsyncMock(return_value=user)

            with pytest.raises(Exception) as exc_info:
                await get_auth_context(request, _make_db(), "tok")

            # DB fallback ran despite state being None...
            mock_fb.assert_awaited_once()
            # ...and its True verdict rejected the token.
            assert "revoked" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_state_false_short_circuits_no_db_call(self, request_factory):
        from src.app.api.deps.auth import get_auth_context

        state = MagicMock()
        state.is_token_blacklisted = False
        request = request_factory(state)
        payload = {"jti": "j1", "token_type": "access", "sub": "user@example.com", "token_version": 0}
        user = {
            "id": 1,
            "email": "user@example.com",
            "is_active": True,
            "is_deleted": False,
            "token_version": 0,
            "tenant_id": 5,
        }
        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
            patch(
                "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
                new=AsyncMock(return_value=True),
            ) as mock_fb,
            patch("src.app.api.deps.auth.get_tenant_context", return_value=None),
            patch("src.app.api.deps.auth.UserService") as mock_us,
        ):
            svc = mock_us.return_value
            svc.get_user_auth_info = AsyncMock(return_value=user)
            ctx = await get_auth_context(request, _make_db(), "tok")
            assert ctx.user.id == 1
            mock_fb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_absent_runs_db_fallback(self, request_factory):
        """No attribute at all (e.g. non-middleware entry) still falls back."""
        from src.app.api.deps.auth import get_auth_context

        state = MagicMock(spec=[])  # no attributes at all
        request = request_factory(state)
        payload = {"jti": "j1", "token_type": "access", "sub": "user@example.com", "token_version": 0}
        user = {
            "id": 1,
            "email": "user@example.com",
            "is_active": True,
            "is_deleted": False,
            "token_version": 0,
            "tenant_id": 5,
        }
        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
            patch(
                "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
                new=AsyncMock(return_value=True),
            ) as mock_fb,
            patch("src.app.api.deps.auth.UserService") as mock_us,
        ):
            svc = mock_us.return_value
            svc.get_user_auth_info = AsyncMock(return_value=user)
            with pytest.raises(Exception) as exc_info:
                await get_auth_context(request, _make_db(), "tok")
            mock_fb.assert_awaited_once()
            assert "revoked" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rate_limit_middleware_stores_tri_state_none(self):
        """Middleware persists None (not False) when the probe returns None."""
        from unittest.mock import patch as _patch

        from src.app.middleware.rate_limit import RateLimitMiddleware

        mw = RateLimitMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"Authorization": "Bearer sometoken"}
        request.state = MagicMock(spec=["is_token_blacklisted"])
        request.url.path = "/api/v1/x"

        with (
            _patch(
                "src.app.middleware.rate_limit.extract_jwt_claims_to_state",
                new_callable=AsyncMock,
                return_value={"sub": "42", "tier_name": "premium", "jti": "j9"},
            ),
            _patch("src.app.middleware.rate_limit.get_cached_jwt_payload", return_value=None),
            _patch(
                "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
                new_callable=AsyncMock,
                return_value=None,
            ),
            _patch("src.app.core.security.rate_limit_cache.get_tier_policy", new_callable=AsyncMock, return_value=[]),
        ):
            await mw._get_rate_limit_config(request)

        assert request.state.is_token_blacklisted is None

    @pytest.mark.asyncio
    async def test_breaker_open_fail_closed_returns_true_flag(self):
        """Circuit breaker open + fail_closed setting -> (None, True)."""
        breaker = MagicMock()
        breaker.can_attempt.return_value = False
        with (
            _patch_setting("TOKEN_CACHE_FAILURE_MODE", "fail_closed"),
            patch("src.app.core.security.token_blacklist_cache._circuit_breaker", breaker),
        ):
            client, fail_closed = _get_available_redis_client_for_blacklist()
        assert client is None
        assert fail_closed is True

    @pytest.mark.asyncio
    async def test_breaker_open_fail_open_returns_none_result(self):
        """Circuit breaker open + fail_open setting -> probe yields None."""
        breaker = MagicMock()
        breaker.can_attempt.return_value = False
        with (
            _patch_setting("TOKEN_CACHE_FAILURE_MODE", "fail_open"),
            patch("src.app.core.security.token_blacklist_cache._circuit_breaker", breaker),
        ):
            result = await is_token_blacklisted("some-token")
        assert result is None


def _make_db() -> MagicMock:
    return MagicMock()


def _patch_setting(name, value):
    """Patch settings.auth.TOKEN_CACHE_FAILURE_MODE."""
    settings_obj = MagicMock()
    setattr(settings_obj, name, value)
    return patch("src.app.core.config.settings.auth", settings_obj)
