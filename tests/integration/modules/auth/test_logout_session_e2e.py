"""P1-2 e2e: the server-side session entry is actually deleted on cookie logout.

Drives the REAL ``session_manager.invalidate_session`` against a stateful
Redis double, so the assertion is about the stored session entry rather than
about a mock being called.

Redis is unreachable in this environment, and the shared ``FakeRedis`` in
conftest answers ``hexists`` with a constant 1 (every session always valid).
That is fine for tests which never seed a session, but it cannot observe a
deletion. So this file carries its own minimal, stateful double rather than
changing the shared one, which would silently alter unrelated suites.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

SESSION_SID = "browser-session-abc123"
USER_ID = 7


def _token(sid: str, token_type: str = "access") -> str:
    return jwt.encode(
        {
            "sub": str(USER_ID),
            "sid": sid,
            "type": token_type,
            "exp": int(time.time()) + 3600,
        },
        "test-signing-key",
        algorithm="HS256",
    )


def _user_data():
    data = MagicMock()
    data.username_or_email = "operator@example.com"
    return data


def _db_user():
    return {"id": USER_ID, "tenant_id": 1, "username": "operator"}


def _request():
    request = MagicMock()
    request.headers = {"user-agent": "pytest"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


def _logout_patches(auth_service):
    return (
        patch(
            "src.app.modules.auth.service.verify_token",
            new_callable=AsyncMock,
            return_value=_user_data(),
        ),
        patch("src.app.modules.auth.service.blacklist_tokens", new_callable=AsyncMock),
        patch.object(auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()),
        patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock),
    )


class _StatefulRedis:
    """Minimal in-memory Redis supporting the session-registry commands."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def hexists(self, key, field):
        return 1 if field in self.hashes.get(key, {}) else 0

    async def hdel(self, key, *fields):
        entries = self.hashes.get(key, {})
        return sum(1 for f in fields if entries.pop(f, None) is not None)

    async def zadd(self, key, mapping):
        entries = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in entries:
                added += 1
            entries[member] = score
        return added

    async def zrem(self, key, *members):
        entries = self.zsets.get(key, {})
        return sum(1 for m in members if entries.pop(m, None) is not None)

    def pipeline(self, transaction=True):
        return _StatefulPipeline(self)


class _StatefulPipeline:
    def __init__(self, redis: _StatefulRedis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple]] = []

    def hdel(self, key, *fields):
        self._commands.append(("hdel", (key, *fields)))
        return self

    def zrem(self, key, *members):
        self._commands.append(("zrem", (key, *members)))
        return self

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self._commands.append((name, args))
            return self

        return queue

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def execute(self):
        results = []
        for name, args in self._commands:
            method = getattr(self._redis, name, None)
            results.append(await method(*args) if method else None)
        self._commands = []
        return results


@pytest.fixture
def auth_service():
    from src.app.modules.auth.service import AuthService

    return AuthService.__new__(AuthService)


@pytest.fixture
def session_store():
    """Install the stateful double on the singleton the product code uses.

    _revoke_session reaches the module-level ``session_manager``
    (session_manager.py:541), so the double is installed there. Seeding a
    private instance instead would test a registry production never reads.
    """
    from src.app.core.security import session_manager as sm_module

    original = sm_module.session_manager._redis_client
    store = _StatefulRedis()
    sm_module.session_manager.set_redis_client(store)
    try:
        yield store
    finally:
        sm_module.session_manager.set_redis_client(original)


@pytest.fixture
def manager():
    from src.app.core.security import session_manager as sm_module

    return sm_module.session_manager


class TestSessionEntryActuallyRemoved:
    """The stored session row must be gone, not merely 'invalidate called'."""

    @pytest.mark.asyncio
    async def test_cookie_only_logout_deletes_session_entry(
        self, auth_service, session_store, manager
    ) -> None:
        """Seed a real session row, log out with only the cookie, assert it is gone."""
        hash_key = f"{manager.SESSION_HASH_PREFIX}{USER_ID}"
        order_key = f"{manager.SESSION_ORDER_PREFIX}{USER_ID}"

        await session_store.hset(hash_key, {SESSION_SID: '{"device": "chrome"}'})
        await session_store.zadd(order_key, {SESSION_SID: time.time()})

        # Precondition: the session is live and the manager can find it.
        assert await session_store.hexists(hash_key, SESSION_SID) == 1
        assert await manager.invalidate_session(USER_ID, SESSION_SID) is True
        # Re-seed, then exercise the real logout path.
        await session_store.hset(hash_key, {SESSION_SID: '{"device": "chrome"}'})
        await session_store.zadd(order_key, {SESSION_SID: time.time()})

        p1, p2, p3, p4 = _logout_patches(auth_service)
        with p1, p2, p3, p4:
            result = await auth_service.logout(
                access_token=None,  # the browser case: no header token
                refresh_token=_token(SESSION_SID, "refresh"),
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert result["session_revoked"] is True
        # THE assertion: the row is gone from the store, not just "called".
        assert await session_store.hexists(hash_key, SESSION_SID) == 0
        # And the manager no longer reports it as a live session.
        assert await manager.invalidate_session(USER_ID, SESSION_SID) is False

    @pytest.mark.asyncio
    async def test_header_logout_deletes_session_entry(
        self, auth_service, session_store, manager
    ) -> None:
        """Regression lock: the header path also removes the stored entry."""
        hash_key = f"{manager.SESSION_HASH_PREFIX}{USER_ID}"
        await session_store.hset(hash_key, {SESSION_SID: '{"device": "chrome"}'})
        assert await session_store.hexists(hash_key, SESSION_SID) == 1

        p1, p2, p3, p4 = _logout_patches(auth_service)
        with p1, p2, p3, p4:
            result = await auth_service.logout(
                access_token=_token(SESSION_SID),
                refresh_token=None,
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert result["session_revoked"] is True
        assert await session_store.hexists(hash_key, SESSION_SID) == 0

    @pytest.mark.asyncio
    async def test_logout_without_registered_session_reports_not_revoked(
        self, auth_service, session_store, manager
    ) -> None:
        """The client is told the truth when the registry held no such entry.

        The store is empty, so invalidate_session finds nothing. Reporting
        session_revoked=True here would be the lie P1-2's field exists to
        prevent.
        """
        hash_key = f"{manager.SESSION_HASH_PREFIX}{USER_ID}"
        assert await session_store.hexists(hash_key, SESSION_SID) == 0

        p1, p2, p3, p4 = _logout_patches(auth_service)
        with p1, p2, p3, p4:
            result = await auth_service.logout(
                access_token=None,
                refresh_token=_token(SESSION_SID, "refresh"),
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert result["session_revoked"] is False

    @pytest.mark.asyncio
    async def test_unrelated_session_is_untouched(
        self, auth_service, session_store, manager
    ) -> None:
        """Only the logged-out session dies — no collateral revocation."""
        hash_key = f"{manager.SESSION_HASH_PREFIX}{USER_ID}"
        other_sid = "other-device-session"
        await session_store.hset(hash_key, {SESSION_SID: "{}", other_sid: "{}"})

        p1, p2, p3, p4 = _logout_patches(auth_service)
        with p1, p2, p3, p4:
            await auth_service.logout(
                access_token=None,
                refresh_token=_token(SESSION_SID, "refresh"),
                response=MagicMock(),
                request=_request(),
                db=MagicMock(),
            )

        assert await session_store.hexists(hash_key, SESSION_SID) == 0
        assert await session_store.hexists(hash_key, other_sid) == 1


class TestLogoutLogsDecision:
    """The issue requires the path, sid and outcome to be diagnosable."""

    @pytest.mark.asyncio
    async def test_cookie_logout_logs_sid_and_source(self, auth_service) -> None:
        """Logs token_source, sid and outcome — never a token value."""
        with (
            patch.object(auth_service, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_db_user()),
            patch(
                "src.app.core.security.session_manager.session_manager.invalidate_session",
                new_callable=AsyncMock,
            ),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await auth_service._revoke_session(
                access_token=None,
                user_data=_user_data(),
                db=MagicMock(),
                refresh_token=_token(SESSION_SID, "refresh"),
            )

        mock_logger.info.assert_called()
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["token_source"] == "refresh_token"
        assert kwargs["sid"] == SESSION_SID
        assert kwargs["entry_found"] is True

    @pytest.mark.asyncio
    async def test_no_token_logs_skip_reason(self, auth_service) -> None:
        """A logout that cannot revoke says why."""
        with patch("src.app.modules.auth.service.logger") as mock_logger:
            await auth_service._revoke_session(
                access_token=None, user_data=_user_data(), db=MagicMock(), refresh_token=None
            )

        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["reason"] == "no_token"
        assert kwargs["revoked"] is False
