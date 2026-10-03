"""S-08 — a Redis ERROR is "unavailable", not an unhandled 500.

`SessionManager.is_session_valid` guarded `if not client:` but the client is a
live `Redis` over a connection pool and is never falsy during an outage. A
`ConnectionError` / `TimeoutError` therefore propagated straight out of the
method. The call chain — `core/principal.py:296` → `api/deps/auth.py`
`_resolve_strict_bearer_flow` → `_enforce_session_valid` → `is_session_valid` —
has no try/except on the strict/REQUIRED path, so a Redis TIMEOUT (the common
case, not a full outage) turned every authenticated request into a 500 instead
of the intended 401/`REVOKED_SESSION`.

These tests pin the *behaviour that matters*: a raising client must produce the
same verdict as an absent client, selected by `TOKEN_CACHE_FAILURE_MODE` and
`jwt_exp` — never an exception, and never an accidental accept.

The mutating methods are pinned separately: they must NOT report success on a
Redis failure, or a logout would be acknowledged while the session lived on.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from src.app.core.security import session_manager as sm


class _BoomRedis:
    """A Redis client whose every command raises, as during a real outage."""

    def __getattr__(self, _name: str):
        async def _raise(*_args: Any, **_kwargs: Any):
            raise ConnectionError("redis down")

        return _raise


def _manager(client: Any) -> sm.SessionManager:
    mgr = sm.SessionManager()
    mgr._redis_client = client
    return mgr


@pytest.fixture
def fail_closed(monkeypatch):
    monkeypatch.setattr("src.app.core.config.settings.auth.TOKEN_CACHE_FAILURE_MODE", "fail_closed")


@pytest.fixture
def fail_open(monkeypatch):
    monkeypatch.setattr("src.app.core.config.settings.auth.TOKEN_CACHE_FAILURE_MODE", "fail_open")


class TestIsSessionValidOnRedisError:
    @pytest.mark.asyncio
    async def test_redis_error_does_not_propagate(self, fail_closed):
        """The core S-08 property: no exception escapes the auth path."""
        mgr = _manager(_BoomRedis())
        result = await mgr.is_session_valid(user_id=1, session_id="sess", jwt_exp=time.time() + 300)
        assert result is False

    @pytest.mark.asyncio
    async def test_fail_closed_rejects_even_unexpired_jwt(self, fail_closed):
        """Under fail_closed a Redis error must reject, never accept."""
        mgr = _manager(_BoomRedis())
        assert await mgr.is_session_valid(1, "sess", jwt_exp=time.time() + 300) is False

    @pytest.mark.asyncio
    async def test_fail_open_degraded_accepts_unexpired_jwt(self, fail_open):
        """Under fail_open the documented degraded mode accepts a live JWT."""
        mgr = _manager(_BoomRedis())
        assert await mgr.is_session_valid(1, "sess", jwt_exp=time.time() + 300) is True

    @pytest.mark.asyncio
    async def test_fail_open_rejects_expired_jwt(self, fail_open):
        """Degraded mode is bounded by the JWT expiry window."""
        mgr = _manager(_BoomRedis())
        assert await mgr.is_session_valid(1, "sess", jwt_exp=time.time() - 10) is False

    @pytest.mark.asyncio
    async def test_redis_error_matches_absent_client(self, fail_open, monkeypatch):
        """The error path and the no-client path must agree exactly."""
        boom = await _manager(_BoomRedis()).is_session_valid(1, "sess", jwt_exp=time.time() + 300)

        mgr_none = _manager(None)
        mgr_none._redis_client = sm._UNSET
        monkeypatch.setattr(sm, "get_redis", lambda: None, raising=False)
        absent = await mgr_none.is_session_valid(1, "sess", jwt_exp=time.time() + 300)

        assert boom == absent


class TestMutatorsDoNotFakeSuccess:
    @pytest.mark.asyncio
    async def test_invalidate_session_returns_false_on_error(self):
        mgr = _manager(_BoomRedis())
        assert await mgr.invalidate_session(1, "sess") is False

    @pytest.mark.asyncio
    async def test_invalidate_all_sessions_returns_zero_on_error(self):
        mgr = _manager(_BoomRedis())
        assert await mgr.invalidate_all_sessions(1) == 0


class TestReadersDegradeNot500:
    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_empty_on_error(self):
        assert await _manager(_BoomRedis()).get_active_sessions(1) == []

    @pytest.mark.asyncio
    async def test_count_active_sessions_returns_zero_on_error(self):
        assert await _manager(_BoomRedis()).count_active_sessions(1) == 0


class TestHealthyClientUnaffected:
    @pytest.mark.asyncio
    async def test_hit_returns_true_and_miss_returns_false(self):
        class _OkRedis:
            async def hexists(self, _key: str, _field: str) -> bool:
                return True

        assert await _manager(_OkRedis()).is_session_valid(1, "sess") is True
