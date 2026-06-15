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
