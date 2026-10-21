"""A corrupt L1 tenant entry must never 500 every request for that slug.

The L1 cache is a plain dict of payloads. A payload whose ``id`` is not a
UUID (or that is missing a key) cannot be parsed into a TenantContext. Before
this was handled, the parse happened in TWO places — the first read and the
double-check under the stampede lock — and only the first one was guarded. A
single corrupt entry therefore turned every request for that slug into a 500
until the TTL aged it out.

The entry is now dropped at the point of detection, so the lookup falls
through to L2/L3 and the next write repairs the cache.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.db.rls import RLSAuditFailureError
from src.app.core.tenancy.cache import _L1_TENANT_CACHE, _L1_TENANT_PARSED
from src.app.core.tenancy.resolver import resolve_tenant_by_slug

SLUG = "acme"


@pytest.fixture(autouse=True)
def clear_l1_cache():
    """Clear the L1 cache before and after each test."""
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_PARSED.clear()
    yield
    _L1_TENANT_CACHE.clear()
    _L1_TENANT_PARSED.clear()


def _corrupt_payload() -> dict:
    """A cached payload whose id is not a UUID."""
    return {"id": "not-a-uuid", "slug": SLUG, "name": "Acme", "is_active": True}


def _session_factory(order: list[str] | None = None):
    """A session factory whose query returns no tenant row."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    async def _execute(*args, **kwargs):
        if order is not None:
            order.append("execute")
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


@asynccontextmanager
async def _bypass(session, reason, user_id=None):
    """A bypass that succeeds without writing an audit row."""
    yield session


class _NullLock:
    """A lock that does nothing."""

    async def __aenter__(self) -> "_NullLock":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class TestCorruptL1Entry:
    """The first read: a corrupt entry is dropped, not fatal."""

    @pytest.mark.asyncio
    async def test_corrupt_entry_does_not_break_resolution(self) -> None:
        """The resolution completes instead of raising."""
        _L1_TENANT_CACHE[SLUG] = _corrupt_payload()
        factory, _session = _session_factory()

        with patch("src.app.core.db.rls.bypass_rls", _bypass):
            resolved = await resolve_tenant_by_slug(SLUG, session_factory=factory, redis_client=None)

        assert resolved is None  # the database had no row either

    @pytest.mark.asyncio
    async def test_corrupt_entry_is_dropped_and_replaced(self) -> None:
        """The bad payload is gone; the cache holds the authoritative result."""
        _L1_TENANT_CACHE[SLUG] = _corrupt_payload()
        factory, _session = _session_factory()

        with patch("src.app.core.db.rls.bypass_rls", _bypass):
            await resolve_tenant_by_slug(SLUG, session_factory=factory, redis_client=None)

        assert _L1_TENANT_CACHE.get(SLUG) is None  # negative entry, not the corrupt payload
        assert _L1_TENANT_CACHE.get(SLUG) != _corrupt_payload()

    @pytest.mark.asyncio
    async def test_the_double_check_under_the_lock_is_guarded_too(self) -> None:
        """A corrupt entry that appears while the lock is held is handled.

        The first read misses (the cache is empty), another writer fills it
        with a corrupt payload, and the double-check under the stampede lock
        finds it. That path was the unguarded one.
        """
        factory, _session = _session_factory()

        @asynccontextmanager
        async def _lock_that_injects(slug: str):
            _L1_TENANT_CACHE[SLUG] = _corrupt_payload()
            yield _NullLock()

        with (
            patch("src.app.core.db.rls.bypass_rls", _bypass),
            patch("src.app.core.tenancy.resolver._get_tenant_lock", _lock_that_injects),
        ):
            resolved = await resolve_tenant_by_slug(SLUG, session_factory=factory, redis_client=None)

        assert resolved is None
        assert _L1_TENANT_CACHE.get(SLUG) != _corrupt_payload()


class TestRlsAuditFailureFallback:
    """The fallback query must run in an ASSERTED, unscoped state."""

    @pytest.mark.asyncio
    async def test_fallback_clears_the_rls_context_before_querying(self) -> None:
        """The connection state is asserted, not assumed.

        ``bypass_rls`` clears the bypass flag in its ``finally`` and the
        tenant GUC is empty because resolution precedes tenant binding —
        but "should be" is not an assertion, and a stale scope left on a
        pooled connection would silently narrow the lookup.
        """
        order: list[str] = []
        factory, _session = _session_factory(order)

        @asynccontextmanager
        async def _failing_bypass(session, reason, user_id=None):
            raise RLSAuditFailureError("audit log write failed")
            yield session  # pragma: no cover - the raise above is the point

        clear_mock = AsyncMock(side_effect=lambda *args, **kwargs: order.append("clear"))

        with (
            patch("src.app.core.db.rls.bypass_rls", _failing_bypass),
            patch("src.app.core.db.rls.clear_rls_context", clear_mock),
        ):
            resolved = await resolve_tenant_by_slug(SLUG, session_factory=factory, redis_client=None)

        assert resolved is None
        assert clear_mock.await_count >= 1
        assert order[0] == "clear", "the RLS context must be cleared BEFORE the fallback query"
