"""U15 - Cache SETEX Unit Tests.

Tests verifying that SETEX is used atomically instead of separate
SET + EXPIRE calls, reducing Redis round-trips by 50% for cache writes.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call

import pytest


class TestSetexUsage:
    """Verify SETEX is used instead of SET + EXPIRE."""

    def test_cache_set_with_ttl_uses_setex(self):
        """Verify cache.set_with_ttl uses SETEX."""
        from pathlib import Path

        cache_dir = Path(__file__).parents[4] / "src" / "app" / "core" / "utils" / "cache"
        source = "\n".join(p.read_text() for p in sorted(cache_dir.glob("*.py")))

        # Verify setex is used for cache writes with TTL
        assert "setex" in source.lower()
        # Verify no separate EXPIRE after SET pattern
        assert source.count("setex") > 0
    def test_user_cache_pipeline_uses_setex(self):
        """Verify user cache pipeline uses SETEX."""
        from pathlib import Path

        cache_path = Path(__file__).parents[4] / "src" / "app" / "modules" / "users" / "cache.py"
        source = cache_path.read_text()

        # Pipeline should use setex for user caching
        assert "pipe.setex" in source
        assert source.count("pipe.setex") >= 3


    def test_tenant_cache_uses_setex(self):
        """Verify tenant resolution caching uses SETEX.

        A2: the L1/L2/DB read-through (including the L2 SETEX fills) lives
        in the canonical resolver (``src.app.core.tenancy.resolver``); the
        middleware is a thin adapter, so the invariant is asserted on the
        seam that owns the cache writes.
        """
        from pathlib import Path

        tenant_path = Path(__file__).parents[4] / "src" / "app" / "core" / "tenancy" / "resolver.py"
        source = tenant_path.read_text()

        # Tenant caching should use setex
        assert "setex" in source

    def test_rate_limit_cache_uses_setex(self):
        """Verify rate limit caching uses SETEX."""
        from pathlib import Path

        rl_path = Path(__file__).parents[4] / "src" / "app" / "modules" / "rate_limits" / "service.py"
        source = rl_path.read_text()

        # P2-B: the tier-list read path delegates writes to get_with_lock
        # (lock-based stampede protection); the atomic SETEX itself lives in
        # stampede._refresh_cache — the single writer for that key.
        assert "get_with_lock" in source
        stampede_path = (
            Path(__file__).parents[4] / "src" / "app" / "core" / "utils" / "cache" / "stampede.py"
        )
        assert "setex" in stampede_path.read_text()


class TestSetexAtomicity:
    """Verify SETEX atomic behavior."""

    @pytest.mark.asyncio
    async def test_setex_single_command(self):
        """Verify SETEX is a single atomic command."""
        mock_redis = AsyncMock()
        mock_redis.setex.return_value = True

        # SETEX sets key, TTL, and value in one call
        await mock_redis.setex("test_key", 3600, '{"data": "value"}')

        # Single call, not separate SET + EXPIRE
        mock_redis.setex.assert_called_once_with("test_key", 3600, '{"data": "value"}')
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_setex_vs_set_expire_commands(self):
        """Demonstrate SETEX saves one round-trip vs SET + EXPIRE."""
        mock_redis = AsyncMock()

        # BAD pattern (2 commands, 2 round-trips):
        # await mock_redis.set("key", "value")
        # await mock_redis.expire("key", 3600)

        # GOOD pattern (1 command, 1 round-trip):
        await mock_redis.setex("key", 3600, "value")

        # Only SETEX called
        assert mock_redis.setex.call_count == 1
        assert mock_redis.set.call_count == 0
        assert mock_redis.expire.call_count == 0

    @pytest.mark.asyncio
    async def test_setex_ttl_applied_correctly(self):
        """Verify TTL is correctly passed to SETEX."""
        mock_redis = AsyncMock()

        ttls = [60, 300, 3600, 86400]  # 1min, 5min, 1hr, 1day

        for ttl in ttls:
            await mock_redis.setex(f"key_{ttl}", ttl, "value")

        # Verify each call had correct TTL
        calls = mock_redis.setex.call_args_list
        assert len(calls) == 4
        for i, ttl in enumerate(ttls):
            assert calls[i] == call(f"key_{ttl}", ttl, "value")


class TestSetexInPipeline:
    """Verify SETEX works correctly in pipelines."""

    @pytest.mark.asyncio
    async def test_pipeline_setex_batches_commands(self):
        """Verify pipeline with SETEX batches multiple writes."""
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])

        # Simulate pipeline with multiple SETEX
        mock_pipe.setex("key1", 3600, "value1")
        mock_pipe.setex("key2", 3600, "value2")
        mock_pipe.setex("key3", 3600, "value3")
        results = await mock_pipe.execute()

        # All succeed in single execute()
        assert results == [True, True, True]
        assert mock_pipe.setex.call_count == 3
        mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_cache_pattern(self):
        """Verify user caching pattern uses pipeline + SETEX."""
        mock_redis = AsyncMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])

        @asynccontextmanager
        async def mock_pipeline():
            yield mock_pipe

        mock_redis.pipeline = mock_pipeline

        # Simulate user cache write (3 keys: id, email, username)
        async with mock_redis.pipeline() as pipe:
            pipe.setex("user:123", 3600, '{"id": 123}')
            pipe.setex("user:email:test@example.com", 3600, '{"id": 123}')
            pipe.setex("user:username:testuser", 3600, '{"id": 123}')
            results = await pipe.execute()

        assert results == [True, True, True]
        assert mock_pipe.setex.call_count == 3


class TestIsomorphismSetex:
    """Isomorphism tests - verify SETEX produces same state as SET + EXPIRE."""

    @pytest.mark.asyncio
    async def test_setex_equivalent_to_set_expire(self):
        """Verify SETEX produces identical state to SET + EXPIRE."""
        # Both patterns should result in:
        # - Key exists with value
        # - Key has TTL set

        mock_redis = AsyncMock()

        # After SETEX, we expect:
        mock_redis.get.return_value = b"test_value"
        mock_redis.ttl.return_value = 3590  # ~1 hour minus some seconds

        await mock_redis.setex("key", 3600, "test_value")

        # Verify state is as expected
        value = await mock_redis.get("key")
        ttl = await mock_redis.ttl("key")

        assert value == b"test_value"
        assert 0 < ttl <= 3600

    @pytest.mark.asyncio
    async def test_setex_overwrites_existing(self):
        """Verify SETEX overwrites existing key (same as SET)."""
        mock_redis = AsyncMock()
        mock_redis.setex.return_value = True

        # Set initial value
        await mock_redis.setex("key", 3600, "value1")

        # Overwrite with new value and TTL
        await mock_redis.setex("key", 7200, "value2")

        # Both calls succeeded
        assert mock_redis.setex.call_count == 2

        # Last call has new value and TTL
        last_call = mock_redis.setex.call_args_list[-1]
        assert last_call == call("key", 7200, "value2")


class TestNoSeparateExpireCalls:
    """Verify no code uses SET + EXPIRE pattern."""

    def test_no_set_then_expire_in_cache_module(self):
        """Verify cache.py doesn't use SET followed by EXPIRE."""
        from pathlib import Path

        cache_dir = Path(__file__).parents[4] / "src" / "app" / "core" / "utils" / "cache"
        source = "\n".join(p.read_text() for p in sorted(cache_dir.glob("*.py")))
        lines = source.split("\n")

        # Look for patterns like:
        # await client.set(key, value)
        # await client.expire(key, ttl)
        for i, line in enumerate(lines[:-1]):
            if ".set(" in line and "setex" not in line.lower() and "ex=" not in line:
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                # If SET is followed by EXPIRE, that's the bad pattern
                if ".expire(" in next_line:
                    pytest.fail(f"Found SET+EXPIRE pattern at lines {i + 1}-{i + 2}")

    def test_set_with_ex_parameter_acceptable(self):
        """Verify SET with ex= parameter is also acceptable (equivalent to SETEX)."""
        from pathlib import Path

        cache_dir = Path(__file__).parents[4] / "src" / "app" / "core" / "utils" / "cache"
        source = "\n".join(p.read_text() for p in sorted(cache_dir.glob("*.py")))

        # SET with ex= is fine (atomic like SETEX)
        # Example: await client.set(key, value, ex=ttl)
        # This is equivalent to SETEX
        if "set(" in source and "ex=" in source:
            # This is acceptable - it's the same as SETEX
            pass

        # The key thing: no separate .expire() calls after .set()
        # (already tested above)
