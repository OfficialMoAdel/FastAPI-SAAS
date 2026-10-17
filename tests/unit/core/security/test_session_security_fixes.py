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
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

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




def _dumpable(user: dict | None):
    """Wrap a raw user dict the way UserDirectory seam methods return it.

    Seam methods return validated models; callers immediately ``.model_dump()``.
    Mocking at the seam therefore means returning an object whose
    ``model_dump()`` yields the fixture dict (``None`` stays ``None``).
    """
    if user is None:
        return None
    m = MagicMock()
    m.model_dump.return_value = user
    return m

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
                "src.app.modules.auth.service.UserDirectory",
            ) as mock_ud,
            patch(
                "src.app.core.security.jwt_utils.decode_jwt_token",
                new_callable=AsyncMock,
                return_value={"sid": old_sid, "absolute_exp": 123.0, "iat": 456.0},
            ),
            # Seam runs for real; only its I/O boundaries are faked.
            # (Whole-TokenIssuer mock would bypass the wiring under test.)
            patch(
                "src.app.modules.auth.services.tokens.TokenIssuer.rotate_token_pair",
                new_callable=AsyncMock,
                return_value=("new_access", "new_refresh"),
            ),
            patch("src.app.core.security.blacklist_token", new_callable=AsyncMock),
            patch(
                "src.app.core.utils.cache.client", None
            ),  # skip refresh lock
            patch(
                "src.app.modules.auth.services.tokens.register_login_session",
                new_callable=AsyncMock,
            ),
            patch("src.app.core.events.event_bus") as mock_bus,
            patch(
                "src.app.core.security.session_manager.session_manager"
            ) as mock_sm,
        ):
            mock_bus.publish = AsyncMock()
            mock_sm.invalidate_session = AsyncMock(return_value=True)
            mock_ud.return_value.auth_info = AsyncMock(return_value=_dumpable(db_user))

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


def _bound_tenant():
    """A real, active tenant — the state production actually reaches.

    P1-6: the tenant middleware binds a resolved tenant or the NO_TENANT
    sentinel; it never leaves the context unset for a request that reaches a
    guard. Tests binding None modelled an unreachable state.
    """
    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=UUID(int=5),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


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
            patch("src.app.api.deps.auth.UserDirectory") as mock_us,
        ):
            svc = mock_us.return_value
            svc.auth_info = AsyncMock(return_value=_dumpable(user))

            with pytest.raises(Exception) as exc_info:
                await get_auth_context(request, "tok")

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
            "tenant_id": UUID(int=5),
        }
        with (
            patch("src.app.api.deps.auth.get_cached_jwt_payload", return_value=payload),
            patch(
                "src.app.api.deps.auth._is_token_blacklisted_with_fallback",
                new=AsyncMock(return_value=True),
            ) as mock_fb,
            patch(
                "src.app.api.deps.auth.get_current_tenant",
                return_value=_bound_tenant(),
            ),
            patch("src.app.api.deps.auth.UserDirectory") as mock_us,
        ):
            svc = mock_us.return_value
            svc.auth_info = AsyncMock(return_value=_dumpable(user))
            ctx = await get_auth_context(request, "tok")
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
            patch("src.app.api.deps.auth.UserDirectory") as mock_us,
        ):
            svc = mock_us.return_value
            svc.auth_info = AsyncMock(return_value=_dumpable(user))
            with pytest.raises(Exception) as exc_info:
                await get_auth_context(request, "tok")
            mock_fb.assert_awaited_once()
            assert "revoked" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rate_limit_middleware_stores_tri_state_none(self):
        """Admission persists None (not False) when the probe returns None."""
        from unittest.mock import patch as _patch

        from src.app.core.security.request_admission import RequestAdmissionService

        admission = RequestAdmissionService()
        request = MagicMock()
        request.headers = {"Authorization": "Bearer sometoken"}
        request.state = MagicMock(spec=["is_token_blacklisted"])
        request.url.path = "/api/v1/x"

        with (
            _patch(
                "src.app.core.security.request_admission.extract_jwt_claims_to_state",
                new_callable=AsyncMock,
                return_value={"sub": "42", "tier_name": "premium", "jti": "j9"},
            ),
            _patch("src.app.core.security.request_admission.get_cached_jwt_payload", return_value=None),
            _patch(
                "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
                new_callable=AsyncMock,
                return_value=None,
            ),
            _patch("src.app.core.security.rate_limit_cache.get_tier_policy", new_callable=AsyncMock, return_value=[]),
        ):
            ctx = await admission._get_rate_limit_config(request)
            await admission._resolve_legacy_config(request, ctx)

        assert request.state.is_token_blacklisted is None

    @pytest.mark.asyncio
    async def test_breaker_open_fail_closed_returns_true_flag(self):
        """Circuit breaker open + fail_closed setting -> (None, True)."""
        breaker = MagicMock()
        breaker.is_closed = False  # circuit open -> gate blocks Redis
        with (
            _patch_setting("TOKEN_CACHE_FAILURE_MODE", "fail_closed"),
            patch("src.app.core.security.token_blacklist_cache.token_cache_circuit", breaker),
        ):
            client, fail_closed = _get_available_redis_client_for_blacklist()
        assert client is None
        assert fail_closed is True

    @pytest.mark.asyncio
    async def test_breaker_open_fail_open_returns_none_result(self):
        """Circuit breaker open + fail_open setting -> probe yields None."""
        breaker = MagicMock()
        breaker.is_closed = False  # circuit open -> gate blocks Redis
        with (
            _patch_setting("TOKEN_CACHE_FAILURE_MODE", "fail_open"),
            patch("src.app.core.security.token_blacklist_cache.token_cache_circuit", breaker),
        ):
            result = await is_token_blacklisted("some-token")
        assert result is None


def _make_db() -> MagicMock:
    return MagicMock()


@asynccontextmanager
async def _stub_db_context() -> Any:
    yield _make_db()


@pytest.fixture(autouse=True)
def _serve_lazily_acquired_db() -> Any:
    """Serve the session ``get_auth_context`` now opens for itself.

    S-1 moved session acquisition out of the dependency signature and into
    ``get_db_context()`` at the point of use. The collaborators that touch the
    session are mocked in these tests, so a stub context is enough.
    """
    with patch("src.app.core.db.database.get_db_context", _stub_db_context):
        yield


def _patch_setting(name, value):
    """Patch settings.auth.TOKEN_CACHE_FAILURE_MODE."""
    settings_obj = MagicMock()
    setattr(settings_obj, name, value)
    return patch("src.app.core.config.settings.auth", settings_obj)


class TestTokenVersionSingleflight:
    """P6: concurrent Redis misses for the same user must collapse into ONE
    DB lookup instead of stampeding SELECT users."""

    @pytest.mark.asyncio
    async def test_concurrent_misses_single_db_lookup(self):
        from src.app.core.security.jwt_service import TokenType, verify_token

        payload = {
            "sub": "u",
            "token_type": "access",
            "jti": "abc",
            "token_version": 7,
            "tenant_id": 1,
        }
        db = MagicMock()
        redis_client = MagicMock()
        redis_client.get = AsyncMock(return_value=None)  # L2 miss for everyone
        redis_client.set = AsyncMock()

        get_calls = 0

        async def slow_get(*args, **kwargs):
            nonlocal get_calls
            get_calls += 1
            await asyncio.sleep(0.05)  # widen the herd window
            return {"token_version": 7}

        with (
            patch("src.app.core.security.jwt_service.jwt.decode", return_value=payload),
            patch(
                "src.app.core.security.jwt_service.is_token_blacklisted",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.core.utils.cache.client", redis_client),
            patch("src.app.modules.users.crud.crud_users.get", new=slow_get),
        ):
            results = await asyncio.gather(
                *(verify_token("tok", TokenType.ACCESS, db) for _ in range(10))
            )

        assert all(r is not None for r in results)
        assert get_calls == 1  # singleflight collapsed the 10-way herd
        redis_client.set.assert_awaited()  # winner backfilled the cache
