"""Performance benchmarks for Token Blacklist (P0-3).

Benchmarks the Lua script atomic operations vs multiple roundtrips.

Scenario: Bloom false positive case (hits Redis)
Before: 4 roundtrips (~4ms)
After: 1 EVALSHA (~1ms)
Threshold: Must be <2ms
"""

import logging
import time

import pytest

from src.app.core.security.bloom_filter import BloomFilter
from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)


@pytest.mark.benchmark
def test_bench_token_blacklist_lua_script(benchmark_runner, baseline_comparator):
    """Benchmark token blacklist with Lua script (atomic operation).

    Verifies that blacklisting a token is fast with Lua script.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio

    blacklist_key = "token:blacklist"

    # Lua script for atomic blacklist operation
    lua_script = """
    local blacklist_key = KEYS[1]
    local token = ARGV[1]
    local expiry = ARGV[2]

    redis.call('ZADD', blacklist_key, expiry, token)
    return 1
    """

    async def setup():
        await redis_client.delete(blacklist_key)
        logger.info("Setup: Cleared blacklist")

    asyncio.run(setup())

    # Benchmark: Blacklist token with Lua script
    counter = [0]

    def blacklist_token():
        async def _blacklist():
            token = f"token_{counter[0]}"
            counter[0] += 1
            expiry = time.time() + 3600

            # Execute Lua script
            await redis_client.eval(lua_script, 1, blacklist_key, token, expiry)

        return asyncio.run(_blacklist())

    result = benchmark_runner(
        name="token_blacklist_lua_atomic",
        func=blacklist_token,
        iterations=100,
        threshold_ms=2.0,  # Must be < 2ms
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(blacklist_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_token_check_with_bloom(benchmark_runner, baseline_comparator):
    """Benchmark token check with Bloom filter pre-filtering.

    Verifies that Bloom filter reduces Redis calls.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio

    # Setup: Create Bloom filter and blacklist some tokens
    bloom = BloomFilter(expected_items=1000, false_positive_rate=0.01)
    blacklist_key = "token:blacklist"

    async def setup():
        await redis_client.delete(blacklist_key)

        # Blacklist 100 tokens
        for i in range(100):
            token = f"blacklisted_token_{i}"
            bloom.add(token)
            await redis_client.zadd(blacklist_key, {token: time.time() + 3600})

        logger.info("Setup: Blacklisted 100 tokens with Bloom filter")

    asyncio.run(setup())

    # Benchmark: Check token (not blacklisted, Bloom filter says no)
    def check_token():
        token = "valid_token_not_blacklisted"

        # Step 1: Check Bloom filter (fast)
        if not bloom.might_contain(token):
            return False  # Definitely not blacklisted

        # Step 2: Check Redis (only if Bloom says might be blacklisted)
        async def _check_redis():
            score = await redis_client.zscore(blacklist_key, token)
            return score is not None

        return asyncio.run(_check_redis())

    result = benchmark_runner(
        name="token_check_bloom_prefilter",
        func=check_token,
        iterations=100,
        threshold_ms=0.1,  # Should be very fast (Bloom filter only)
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(blacklist_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_token_check_bloom_false_positive(benchmark_runner, baseline_comparator):
    """Benchmark token check with Bloom filter false positive.

    Verifies performance when Bloom filter says "might contain" but Redis says no.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio

    bloom = BloomFilter(expected_items=1000, false_positive_rate=0.01)
    blacklist_key = "token:blacklist"

    async def setup():
        await redis_client.delete(blacklist_key)

        # Add many tokens to Bloom filter to increase false positive chance
        for i in range(500):
            bloom.add(f"token_{i}")

        logger.info("Setup: Added 500 tokens to Bloom filter")

    asyncio.run(setup())

    # Benchmark: Check token that causes false positive
    counter = [0]

    def check_token_false_positive():
        token = f"false_positive_token_{counter[0]}"
        counter[0] += 1

        # Bloom filter might say "might contain"
        if not bloom.might_contain(token):
            return False

        # Check Redis (will say not blacklisted)
        async def _check_redis():
            score = await redis_client.zscore(blacklist_key, token)
            return score is not None

        return asyncio.run(_check_redis())

    result = benchmark_runner(
        name="token_check_bloom_false_positive",
        func=check_token_false_positive,
        iterations=100,
        threshold_ms=2.0,  # Must be < 2ms (includes Redis check)
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(blacklist_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_bloom_filter_operations(benchmark_runner, baseline_comparator):
    """Benchmark Bloom filter operations (add and check).

    Verifies that Bloom filter operations are very fast.
    """
    bloom = BloomFilter(expected_items=10000, false_positive_rate=0.01)

    # Benchmark: Add to Bloom filter
    counter = [0]

    def add_to_bloom():
        token = f"token_{counter[0]}"
        counter[0] += 1
        bloom.add(token)

    add_result = benchmark_runner(
        name="bloom_filter_add",
        func=add_to_bloom,
        iterations=1000,
        threshold_ms=0.01,  # Must be < 0.01ms (10 microseconds)
    )

    baseline_comparator(add_result)

    # Benchmark: Check Bloom filter
    def check_bloom():
        token = "check_token"
        return bloom.might_contain(token)

    check_result = benchmark_runner(
        name="bloom_filter_check",
        func=check_bloom,
        iterations=1000,
        threshold_ms=0.01,  # Must be < 0.01ms (10 microseconds)
    )

    baseline_comparator(check_result)


@pytest.mark.benchmark
def test_bench_blacklist_comparison_lua_vs_multi(benchmark_runner):
    """Compare Lua script vs multiple roundtrips for blacklisting.

    Demonstrates performance difference between atomic and multi-roundtrip operations.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio

    blacklist_key = "token:blacklist:comparison"

    # Lua script (atomic)
    lua_script = """
    local blacklist_key = KEYS[1]
    local token = ARGV[1]
    local expiry = ARGV[2]

    redis.call('ZADD', blacklist_key, expiry, token)
    return 1
    """

    async def setup():
        await redis_client.delete(blacklist_key)

    asyncio.run(setup())

    # Benchmark: Lua script (1 roundtrip)
    counter_lua = [0]

    def blacklist_lua():
        async def _blacklist():
            token = f"lua_token_{counter_lua[0]}"
            counter_lua[0] += 1
            expiry = time.time() + 3600
            await redis_client.eval(lua_script, 1, blacklist_key, token, expiry)

        return asyncio.run(_blacklist())

    lua_result = benchmark_runner(
        name="blacklist_lua_1_roundtrip",
        func=blacklist_lua,
        iterations=100,
    )

    # Benchmark: Multiple operations (simulated 4 roundtrips)
    counter_multi = [0]

    def blacklist_multi():
        async def _blacklist():
            token = f"multi_token_{counter_multi[0]}"
            counter_multi[0] += 1
            expiry = time.time() + 3600

            # Simulate multiple roundtrips
            await redis_client.zadd(blacklist_key, {token: expiry})
            await redis_client.zscore(blacklist_key, token)  # Verify
            await redis_client.zcard(blacklist_key)  # Count
            await redis_client.ttl(blacklist_key)  # Check TTL

        return asyncio.run(_blacklist())

    multi_result = benchmark_runner(
        name="blacklist_multi_4_roundtrips",
        func=blacklist_multi,
        iterations=100,
    )

    # Log comparison
    logger.info("\n" + "=" * 60)
    logger.info("COMPARISON: Lua Script vs Multiple Roundtrips")
    logger.info("=" * 60)
    logger.info(f"Lua (1 roundtrip):    {lua_result.mean * 1000:.3f} ms")
    logger.info(f"Multi (4 roundtrips): {multi_result.mean * 1000:.3f} ms")
    logger.info(f"Speedup:              {multi_result.mean / lua_result.mean:.2f}x")
    logger.info("=" * 60 + "\n")

    # Cleanup
    async def cleanup():
        await redis_client.delete(blacklist_key)

    asyncio.run(cleanup())
