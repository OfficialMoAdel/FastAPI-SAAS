"""P3-2: Tenant Middleware Serialization Tests.

Verifies that tenant caching uses bytes directly instead of string conversion.
"""

from unittest.mock import AsyncMock

import pytest

from src.app.core.json_utils import json_dumps


def _bypass_rls_stub():
    """A no-op stand-in for the resolver's RLS bypass context manager."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _stub(session, *args, **kwargs):
        yield session

    return _stub


def _run_resolve(resolver, slug, session_factory, redis):
    """Drive resolve_tenant_by_slug synchronously for a sync test body."""
    import asyncio

    return asyncio.run(
        resolver.resolve_tenant_by_slug(
            slug,
            session_factory=session_factory,
            redis_client=redis,
        )
    )


class TestTenantSerialization:
    """Tests for tenant middleware serialization efficiency."""

    def test_json_dumps_returns_bytes_by_default(self) -> None:
        """Verify json_dumps returns bytes without as_str flag."""
        data = {"id": 1, "slug": "test", "is_active": True}

        result = json_dumps(data)

        assert isinstance(result, bytes)

    def test_json_dumps_with_as_str_returns_string(self) -> None:
        """Verify json_dumps returns string with as_str=True."""
        data = {"id": 1, "slug": "test", "is_active": True}

        result = json_dumps(data, as_str=True)

        assert isinstance(result, str)

    def test_bytes_avoid_decode_overhead(self) -> None:
        """Verify bytes skip the decode step."""
        data = {"id": 1, "slug": "test", "is_active": True}

        # Without as_str - no decode
        bytes_result = json_dumps(data)

        # With as_str - has decode overhead
        str_result = json_dumps(data, as_str=True)

        # Both represent same data
        assert bytes_result == str_result.encode()

        # But bytes_result avoids the decode step
        assert isinstance(bytes_result, bytes)

    def test_tenant_resolver_writes_bytes_to_l2(self) -> None:
        """The L2 tenant cache must store bytes, not a decoded string.

        Replaces a source-text scan (``inspect.getsource``) whose two
        ``source.count(...)`` expressions were discarded, whose
        ``assert "json_dumps(cache_data)" in source or "json_dumps(" in
        source`` was tautological (the second disjunct subsumes the first),
        and whose guard loop hunted ``"serialized = json_dumps("`` — a
        string that occurs zero times in the module, so the loop body never
        ran. The as_str invariant it claimed to pin was entirely unverified.

        This drives the real resolver and asserts the payload handed to
        Redis, which is the property that actually matters.
        """
        import contextlib
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.app.core.tenancy import resolver as tenant

        slug = "acme-corp"
        tenant.invalidate_tenant_l1(slug)

        row = MagicMock()
        row.id = "11111111-1111-1111-1111-111111111111"
        row.slug = slug
        row.name = "ACME"
        row.is_active = True

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        session.execute = AsyncMock(return_value=result)
        session.connection = AsyncMock()

        @contextlib.asynccontextmanager
        async def _session():
            yield session

        redis = AsyncMock()
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()

        with (
            patch("src.app.core.db.rls.bypass_rls", _bypass_rls_stub()),
            patch("src.app.core.db.rls.clear_rls_context", AsyncMock()),
        ):
            ctx = _run_resolve(tenant, slug, _session, redis)

        assert ctx is not None, "resolver returned None for a valid slug"
        assert ctx.tenant_slug == slug

        # The invariant: the L2 write carries bytes, so the read path
        # (json_loads at resolver.py:180) never needs a decode step.
        setex_calls = redis.setex.call_args_list
        assert setex_calls, "resolver never wrote the L2 tenant cache"
        payload = setex_calls[0].args[2]
        assert isinstance(payload, bytes), (
            f"tenant L2 cache stored {type(payload).__name__}, expected bytes "
            f"(as_str=True would reintroduce the decode step): {payload!r}"
        )

        tenant.invalidate_tenant_l1(slug)

    @pytest.mark.asyncio
    async def test_redis_accepts_bytes(self) -> None:
        """Verify Redis setex accepts bytes directly."""
        mock_redis = AsyncMock()

        # Simulate the fixed pattern
        data = {"id": 1, "slug": "test", "is_active": True}
        cache_data = json_dumps(data)  # Returns bytes

        await mock_redis.setex("tenant:slug:test", 3600, cache_data)

        # Verify setex was called with bytes
        call_args = mock_redis.setex.call_args
        assert isinstance(call_args[0][2], bytes)

    @pytest.mark.asyncio
    async def test_json_loads_accepts_bytes(self) -> None:
        """Verify json_loads can parse bytes from Redis."""
        from src.app.core.json_utils import json_loads

        # Simulate Redis returning bytes
        data = {"id": 1, "slug": "test", "is_active": True}
        cached_bytes = json_dumps(data)

        # json_loads should handle bytes
        result = json_loads(cached_bytes)

        assert result == data
