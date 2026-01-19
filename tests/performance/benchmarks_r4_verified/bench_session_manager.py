"""Performance benchmarks for Session Manager (P0-2).

Benchmarks the O(1) Redis Hash operations vs O(N) list operations.

Scenario: User with 100 active sessions
Before: O(N) deserialization (~10ms)
After: O(1) HLEN (~0.1ms)
Threshold: Must be <1ms
"""

import logging

import pytest

from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)


@pytest.mark.benchmark
def test_bench_session_count_o1(benchmark_runner, baseline_comparator):
    """Benchmark O(1) session count with Redis HLEN.

    Verifies that counting sessions is fast with Redis Hash.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    # Setup: Create 100 sessions in Redis Hash
    user_id = "bench_user_100_sessions"
    session_key = f"session:user:{user_id}"

    import asyncio

    async def setup():
        # Clear any existing data
        await redis_client.delete(session_key)

        # Create 100 sessions
        for i in range(100):
            session_id = f"session_{i}"
            session_data = f'{{"session_id": "{session_id}", "created_at": "2026-01-18"}}'
            await redis_client.hset(session_key, session_id, session_data)

        logger.info("Setup: Created 100 sessions in Redis Hash")

    asyncio.run(setup())

    # Benchmark: Count sessions with HLEN
    def count_sessions():
        async def _count():
            return await redis_client.hlen(session_key)

        return asyncio.run(_count())

    result = benchmark_runner(
        name="session_count_o1_hlen",
        func=count_sessions,
        iterations=100,
        threshold_ms=1.0,  # Must be < 1ms
    )

    # Compare with baseline
    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(session_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_session_list_o1(benchmark_runner, baseline_comparator):
    """Benchmark O(1) session list with Redis HGETALL.

    Verifies that listing sessions is fast with Redis Hash.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    # Setup: Create 100 sessions
    user_id = "bench_user_list_100"
    session_key = f"session:user:{user_id}"

    import asyncio

    async def setup():
        await redis_client.delete(session_key)

        for i in range(100):
            session_id = f"session_{i}"
            session_data = f'{{"session_id": "{session_id}", "user_agent": "test", "ip": "127.0.0.1"}}'
            await redis_client.hset(session_key, session_id, session_data)

        logger.info("Setup: Created 100 sessions for list benchmark")

    asyncio.run(setup())

    # Benchmark: List all sessions with HGETALL
    def list_sessions():
        async def _list():
            return await redis_client.hgetall(session_key)

        return asyncio.run(_list())

    result = benchmark_runner(
        name="session_list_o1_hgetall",
        func=list_sessions,
        iterations=100,
        threshold_ms=5.0,  # Must be < 5ms for 100 sessions
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(session_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_session_delete_o1(benchmark_runner, baseline_comparator):
    """Benchmark O(1) session deletion with Redis HDEL.

    Verifies that deleting a session is fast with Redis Hash.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio

    # Benchmark: Delete session with HDEL
    user_id = "bench_user_delete"
    session_key = f"session:user:{user_id}"

    def delete_session():
        async def _delete():
            # Setup: Create session
            session_id = "session_to_delete"
            await redis_client.hset(session_key, session_id, '{"data": "test"}')

            # Delete
            await redis_client.hdel(session_key, session_id)

        return asyncio.run(_delete())

    result = benchmark_runner(
        name="session_delete_o1_hdel",
        func=delete_session,
        iterations=100,
        threshold_ms=1.0,  # Must be < 1ms
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(session_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_session_exists_o1(benchmark_runner, baseline_comparator):
    """Benchmark O(1) session existence check with Redis HEXISTS.

    Verifies that checking if a session exists is fast.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    # Setup: Create sessions
    user_id = "bench_user_exists"
    session_key = f"session:user:{user_id}"

    import asyncio

    async def setup():
        await redis_client.delete(session_key)

        for i in range(100):
            session_id = f"session_{i}"
            await redis_client.hset(session_key, session_id, '{"data": "test"}')

        logger.info("Setup: Created 100 sessions for exists benchmark")

    asyncio.run(setup())

    # Benchmark: Check if session exists
    def check_exists():
        async def _exists():
            return await redis_client.hexists(session_key, "session_50")

        return asyncio.run(_exists())

    result = benchmark_runner(
        name="session_exists_o1_hexists",
        func=check_exists,
        iterations=100,
        threshold_ms=1.0,  # Must be < 1ms
    )

    baseline_comparator(result)

    # Cleanup
    async def cleanup():
        await redis_client.delete(session_key)

    asyncio.run(cleanup())


@pytest.mark.benchmark
def test_bench_session_operations_comparison(benchmark_runner):
    """Compare session operations: Hash vs List (simulated).

    Demonstrates performance difference between O(1) and O(N) operations.
    """
    if not redis_client:
        pytest.skip("Redis not available")

    import asyncio
    import json

    user_id = "bench_comparison"
    hash_key = f"session:user:{user_id}:hash"
    list_key = f"session:user:{user_id}:list"

    # Setup both structures
    async def setup():
        await redis_client.delete(hash_key, list_key)

        # Create 100 sessions in both structures
        for i in range(100):
            session_id = f"session_{i}"
            session_data = {"session_id": session_id, "created_at": "2026-01-18"}

            # Hash structure
            await redis_client.hset(hash_key, session_id, json.dumps(session_data))

            # List structure (old approach)
            await redis_client.lpush(list_key, json.dumps(session_data))

        logger.info("Setup: Created 100 sessions in both Hash and List")

    asyncio.run(setup())

    # Benchmark Hash count (O(1))
    def count_hash():
        async def _count():
            return await redis_client.hlen(hash_key)

        return asyncio.run(_count())

    hash_result = benchmark_runner(
        name="session_count_hash_o1",
        func=count_hash,
        iterations=100,
    )

    # Benchmark List count (O(1) but requires LLEN)
    def count_list():
        async def _count():
            return await redis_client.llen(list_key)

        return asyncio.run(_count())

    list_result = benchmark_runner(
        name="session_count_list_o1",
        func=count_list,
        iterations=100,
    )

    # Benchmark Hash get all (O(N) but single operation)
    def get_all_hash():
        async def _get():
            return await redis_client.hgetall(hash_key)

        return asyncio.run(_get())

    hash_getall_result = benchmark_runner(
        name="session_getall_hash",
        func=get_all_hash,
        iterations=100,
    )

    # Benchmark List get all (O(N) with deserialization)
    def get_all_list():
        async def _get():
            items = await redis_client.lrange(list_key, 0, -1)
            return [json.loads(item) for item in items]

        return asyncio.run(_get())

    list_getall_result = benchmark_runner(
        name="session_getall_list_with_deserialize",
        func=get_all_list,
        iterations=100,
    )

    # Log comparison
    logger.info("\n" + "=" * 60)
    logger.info("COMPARISON: Hash vs List Operations")
    logger.info("=" * 60)
    logger.info(f"Count (Hash):     {hash_result.mean * 1000:.3f} ms")
    logger.info(f"Count (List):     {list_result.mean * 1000:.3f} ms")
    logger.info(f"GetAll (Hash):    {hash_getall_result.mean * 1000:.3f} ms")
    logger.info(f"GetAll (List):    {list_getall_result.mean * 1000:.3f} ms")
    logger.info(f"Speedup (GetAll): {list_getall_result.mean / hash_getall_result.mean:.2f}x")
    logger.info("=" * 60 + "\n")

    # Cleanup
    async def cleanup():
        await redis_client.delete(hash_key, list_key)

    asyncio.run(cleanup())
