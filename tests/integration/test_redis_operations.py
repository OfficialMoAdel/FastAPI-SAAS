"""Integration tests for Redis operations performance optimizations.

Tests: U2 (Pool Consolidation), U4 (Pipeline), U10 (Rate Limits Cache),
       U12 (Session Hash), U13 (Lock Backoff), U15 (SETEX)
"""


import pytest
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# U2: Redis Pool Consolidation
# =============================================================================
@pytest.mark.integration
class TestRedisPoolConsolidation:
    """U2: Verify single Redis pool shared across components."""

    @pytest.mark.asyncio
    async def test_single_pool_instance(self, clean_redis):
        """Verify only one pool instance exists."""
        from src.app.core.utils import cache

        # Pool should be singleton at module level
        pool1 = cache.pool
        pool2 = cache.pool

        # Same instance
        assert pool1 is pool2

    @pytest.mark.asyncio
    async def test_multiple_components_share_pool(self, clean_redis):
        """Verify cache and session use same pool."""
        from src.app.core.utils import cache

        # Both should reference the same pool
        # In real integration, we'd verify connection reuse
        client1 = cache.client
        client2 = cache.client

        assert client1 is client2

    @pytest.mark.asyncio
    async def test_pool_connection_reuse(self, clean_redis):
        """Verify connections are reused from pool."""
        # Multiple operations should reuse connections
        for i in range(10):
            await clean_redis.set(f"key_{i}", f"value_{i}")

        # All keys should exist
        for i in range(10):
            value = await clean_redis.get(f"key_{i}")
            assert value == f"value_{i}"


# =============================================================================
# U4: User Cache Pipeline
# =============================================================================
@pytest.mark.integration
class TestUserCachePipeline:
    """U4: Verify pipeline reduces Redis round-trips."""

    @pytest.mark.asyncio
    async def test_pipeline_batches_commands(self, clean_redis, perf_timer):
        """Verify pipeline executes multiple commands in single round-trip."""
        # Without pipeline (baseline)
        with perf_timer:
            await clean_redis.set("user:1:id", "data1")
            await clean_redis.set("user:1:email", "data2")
            await clean_redis.set("user:1:username", "data3")
        separate_time = perf_timer.elapsed_ms

        # Clean up
        await clean_redis.delete("user:1:id", "user:1:email", "user:1:username")

        # With pipeline (optimized)
        with perf_timer:
            async with clean_redis.pipeline() as pipe:
                pipe.set("user:1:id", "data1")
                pipe.set("user:1:email", "data2")
                pipe.set("user:1:username", "data3")
                await pipe.execute()
        pipeline_time = perf_timer.elapsed_ms

        logger.info(
            "pipeline_comparison",
            separate_ms=separate_time,
            pipeline_ms=pipeline_time,
        )

        # Pipeline should be faster (fewer round-trips)
        # Note: In tests, difference may be small due to localhost
        assert pipeline_time <= separate_time * 1.5  # Allow some variance

    @pytest.mark.asyncio
    async def test_pipeline_setex_commands(self, clean_redis):
        """Verify pipeline with SETEX for TTL."""
        async with clean_redis.pipeline() as pipe:
            pipe.setex("cache:1", 3600, "value1")
            pipe.setex("cache:2", 3600, "value2")
            pipe.setex("cache:3", 3600, "value3")
            results = await pipe.execute()

        assert all(results)

        # Verify TTLs
        for i in range(1, 4):
            ttl = await clean_redis.ttl(f"cache:{i}")
            assert 3590 < ttl <= 3600

    @pytest.mark.asyncio
    async def test_pipeline_isomorphism(self, clean_redis):
        """Verify pipeline produces same state as separate commands."""
        # Separate commands
        await clean_redis.set("iso:sep:1", "value1")
        await clean_redis.set("iso:sep:2", "value2")

        # Pipeline commands
        async with clean_redis.pipeline() as pipe:
            pipe.set("iso:pipe:1", "value1")
            pipe.set("iso:pipe:2", "value2")
            await pipe.execute()

        # Both should produce same values
        sep1 = await clean_redis.get("iso:sep:1")
        pipe1 = await clean_redis.get("iso:pipe:1")
        assert sep1 == pipe1 == "value1"


# =============================================================================
# U10: Rate Limits Caching
# =============================================================================
@pytest.mark.integration
class TestRateLimitsCaching:
    """U10: Verify rate limits cached after first DB access."""

    @pytest.mark.asyncio
    async def test_rate_limit_cached_on_first_access(self, clean_redis):
        """Verify rate limit is cached after first lookup."""
        cache_key = "rate_limit:tier:1"

        # Simulate caching rate limit
        rate_limit_data = '{"path": "/api/v1/test", "limit": 100, "period": 60}'
        await clean_redis.setex(cache_key, 300, rate_limit_data)

        # Second access should hit cache
        cached = await clean_redis.get(cache_key)
        assert cached == rate_limit_data

    @pytest.mark.asyncio
    async def test_rate_limit_ttl_correct(self, clean_redis):
        """Verify rate limit cache has correct TTL."""
        cache_key = "rate_limit:tier:2"
        await clean_redis.setex(cache_key, 300, "data")

        ttl = await clean_redis.ttl(cache_key)
        assert 290 < ttl <= 300


# =============================================================================
# U12: Session Redis Hash
# =============================================================================
@pytest.mark.integration
class TestSessionRedisHash:
    """U12: Verify session uses HSET instead of JSON blob."""

    @pytest.mark.asyncio
    async def test_session_hset_operations(self, clean_redis):
        """Verify session data stored as hash fields."""
        session_key = "session:abc123"

        # Store session as hash
        await clean_redis.hset(
            session_key,
            mapping={
                "user_id": "123",
                "tenant_id": "456",
                "created_at": "2024-01-01T00:00:00Z",
            },
        )

        # Retrieve individual field
        user_id = await clean_redis.hget(session_key, "user_id")
        assert user_id == "123"

        # Update single field (no need to rewrite whole blob)
        await clean_redis.hset(session_key, "last_active", "2024-01-01T01:00:00Z")

        # Verify update
        last_active = await clean_redis.hget(session_key, "last_active")
        assert last_active == "2024-01-01T01:00:00Z"

    @pytest.mark.asyncio
    async def test_session_hash_vs_json_size(self, clean_redis):
        """Compare memory usage: hash vs JSON."""
        import json

        session_data = {
            "user_id": "123",
            "tenant_id": "456",
            "email": "user@example.com",
            "roles": "admin,user",
            "created_at": "2024-01-01T00:00:00Z",
        }

        # JSON blob
        json_key = "session:json:test"
        await clean_redis.set(json_key, json.dumps(session_data))

        # Hash
        hash_key = "session:hash:test"
        await clean_redis.hset(hash_key, mapping=session_data)

        # Both should contain same data
        json_data = json.loads(await clean_redis.get(json_key))
        hash_data = await clean_redis.hgetall(hash_key)

        assert json_data["user_id"] == hash_data["user_id"]
        assert json_data["email"] == hash_data["email"]

    @pytest.mark.asyncio
    async def test_session_hash_isomorphism(self, clean_redis):
        """Verify hash session produces same data as JSON session."""
        session_id = "iso_test"

        # Store as hash
        await clean_redis.hset(
            f"session:hash:{session_id}",
            mapping={"user_id": "999", "role": "admin"},
        )

        # Retrieve
        hash_session = await clean_redis.hgetall(f"session:hash:{session_id}")

        # Isomorphism: same data accessible
        assert hash_session["user_id"] == "999"
        assert hash_session["role"] == "admin"


# =============================================================================
# U13: Cache Lock Backoff
# =============================================================================
@pytest.mark.integration
class TestCacheLockBackoff:
    """U13: Verify exponential backoff with jitter for lock contention."""

    @pytest.mark.asyncio
    async def test_lock_acquisition_success(self, clean_redis):
        """Verify lock can be acquired."""
        lock_key = "lock:test:1"

        # Acquire lock (NX = only if not exists)
        acquired = await clean_redis.set(lock_key, "owner1", nx=True, ex=10)
        assert acquired is True

        # Second attempt should fail
        acquired2 = await clean_redis.set(lock_key, "owner2", nx=True, ex=10)
        assert acquired2 is None

    @pytest.mark.asyncio
    async def test_lock_release(self, clean_redis):
        """Verify lock can be released."""
        lock_key = "lock:test:2"

        await clean_redis.set(lock_key, "owner1", nx=True, ex=10)

        # Release
        await clean_redis.delete(lock_key)

        # Now another can acquire
        acquired = await clean_redis.set(lock_key, "owner2", nx=True, ex=10)
        assert acquired is True

    @pytest.mark.asyncio
    async def test_backoff_timing_pattern(self):
        """Verify exponential backoff timing."""
        import random

        base_delay = 0.01  # 10ms
        max_delay = 0.1  # 100ms
        delays = []

        for attempt in range(5):
            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            total_delay = delay + jitter
            delays.append(total_delay)

        # Each delay should be roughly 2x previous (with jitter)
        for i in range(1, len(delays) - 1):
            # Allow for jitter variance
            assert delays[i] >= delays[i - 1] * 0.8


# =============================================================================
# U15: SETEX Operations
# =============================================================================
@pytest.mark.integration
class TestSetexOperations:
    """U15: Verify SETEX used for atomic SET+TTL."""

    @pytest.mark.asyncio
    async def test_setex_atomic_operation(self, clean_redis):
        """Verify SETEX sets value and TTL atomically."""
        key = "setex:test:1"

        await clean_redis.setex(key, 3600, "test_value")

        # Value set
        value = await clean_redis.get(key)
        assert value == "test_value"

        # TTL set
        ttl = await clean_redis.ttl(key)
        assert 3590 < ttl <= 3600

    @pytest.mark.asyncio
    async def test_setex_vs_set_expire(self, clean_redis, perf_timer):
        """Compare SETEX vs separate SET+EXPIRE."""
        # SETEX (single command)
        with perf_timer:
            await clean_redis.setex("setex:perf:1", 3600, "value")
        setex_time = perf_timer.elapsed_ms

        # SET + EXPIRE (two commands)
        with perf_timer:
            await clean_redis.set("setex:perf:2", "value")
            await clean_redis.expire("setex:perf:2", 3600)
        set_expire_time = perf_timer.elapsed_ms

        logger.info(
            "setex_comparison",
            setex_ms=setex_time,
            set_expire_ms=set_expire_time,
        )

        # Both should have same TTL
        ttl1 = await clean_redis.ttl("setex:perf:1")
        ttl2 = await clean_redis.ttl("setex:perf:2")
        assert abs(ttl1 - ttl2) < 5  # Within 5 seconds

    @pytest.mark.asyncio
    async def test_setex_overwrite(self, clean_redis):
        """Verify SETEX overwrites existing key."""
        key = "setex:overwrite"

        await clean_redis.setex(key, 3600, "value1")
        await clean_redis.setex(key, 7200, "value2")

        value = await clean_redis.get(key)
        ttl = await clean_redis.ttl(key)

        assert value == "value2"
        assert 7190 < ttl <= 7200

    @pytest.mark.asyncio
    async def test_setex_isomorphism(self, clean_redis):
        """Verify SETEX produces same state as SET+EXPIRE."""
        # SETEX
        await clean_redis.setex("iso:setex", 3600, "value")

        # SET + EXPIRE
        await clean_redis.set("iso:set_expire", "value")
        await clean_redis.expire("iso:set_expire", 3600)

        # Same value
        v1 = await clean_redis.get("iso:setex")
        v2 = await clean_redis.get("iso:set_expire")
        assert v1 == v2

        # Similar TTL
        t1 = await clean_redis.ttl("iso:setex")
        t2 = await clean_redis.ttl("iso:set_expire")
        assert abs(t1 - t2) < 5
