import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_benchmark_hierarchical_invalidation():
    """Benchmark Hierarchical Index: 100K keys, invalidate 100 by prefix."""
    print("\n--- Benchmark: Hierarchical Invalidation ---")

    # Setup Mock
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[1, 1])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Scenario: 100k keys total. Target prefix has 100 keys.
    total_keys = 100_000
    target_count = 100

    # 1. Simulate OLD approach (Global Index + Filter)
    # We don't construct the full set to save memory/CPU in test runner,
    # but we simulate the cost of fetching a large set.
    # In reality, fetching 100k keys from Redis takes time (serialization/network).
    # We can't easily mock network time, but we can mock the "filtering" CPU time?
    # Actually, constructing a set of 100k strings in Python is fast enough for a test.

    all_keys = {f"tenant:1:other:{i}".encode() for i in range(total_keys - target_count)}
    target_keys = {f"tenant:1:user:{i}".encode() for i in range(target_count)}
    all_keys.update(target_keys)

    mock_client.smembers.return_value = all_keys

    start = time.perf_counter()
    # Simulate OLD logic
    fetched = await mock_client.smembers("tenant_index:1")
    filter_prefix = b"tenant:1:user:"
    filtered = {k for k in fetched if k.startswith(filter_prefix)}
    async with mock_client.pipeline() as pipe:
        pipe.delete(*filtered)
        await pipe.execute()
    duration_old = time.perf_counter() - start

    # 2. Simulate NEW approach (Sharded Index)
    mock_client.smembers.return_value = target_keys

    start = time.perf_counter()
    # Logic is inside cache.invalidate_tenant_cache
    with patch("src.app.core.utils.cache.client", mock_client):
        await cache.invalidate_tenant_cache(1, "user")
    duration_new = time.perf_counter() - start

    print(f"OLD (Global Index): {duration_old * 1000:.4f} ms")
    print(f"NEW (Sharded Index): {duration_new * 1000:.4f} ms")
    print(f"Speedup: {duration_old / duration_new:.1f}x")

    # Requirement: < 10ms for NEW
    assert duration_new < 0.010, f"New implementation too slow: {duration_new * 1000:.2f}ms"


@pytest.mark.asyncio
async def test_benchmark_pattern_invalidation():
    """Benchmark Pattern Invalidation: 1M keys, 1K matches."""
    print("\n--- Benchmark: Pattern Invalidation ---")

    # Setup Mock
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[1, 1])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Scenario: 1M keys. 1k matches.
    # Generating 1M keys might be slow in Python (taking seconds).
    # The requirement is to benchmark the *logic*.
    # If we generate 1M keys, the test setup might time out or be slow.
    # Let's try 100k first and extrapolate, or just do 1M if environment handles it.
    # 1M strings ~50MB RAM. Python handles it fine.

    total_keys = 1_000_000
    match_count = 1_000

    # 1. Simulate SCAN (O(N))
    # scan has to iterate 1M keys.
    # Default count is usually 10-100.
    # Even with count=1000, that's 1000 roundtrips.
    # We mock scan side_effect.

    # We won't generate actual 1M keys for SCAN to save test time.
    # We will simulate the *calls*.
    # Assume we need 1000 calls to scan 1M keys (1000 keys/call).
    scan_calls_needed = 1000

    scan_batches = []
    # Just return empty list for most calls, except some return matches?
    # Or just return empty to simulate "scanning".
    # We just want to measure the overhead of 1000 await calls.
    for i in range(scan_calls_needed):
        cursor = i + 1 if i < scan_calls_needed - 1 else 0
        scan_batches.append((cursor, []))

    mock_client.scan.side_effect = scan_batches

    with patch("src.app.core.utils.cache.client", mock_client):
        start = time.perf_counter()
        await cache._delete_keys_by_pattern("pattern:*")
        duration_scan = time.perf_counter() - start

    # 2. Simulate SET (O(1))
    # smembers returns 1000 keys.
    target_keys = {f"pattern:{i}".encode() for i in range(match_count)}
    mock_client.smembers = AsyncMock(return_value=target_keys)
    mock_client.scan.side_effect = None  # Reset

    with patch("src.app.core.utils.cache.client", mock_client):
        start = time.perf_counter()
        await cache.invalidate_by_pattern("pattern:*")
        duration_set = time.perf_counter() - start

    print(f"OLD (SCAN 1M keys): {duration_scan * 1000:.4f} ms (Simulated {scan_calls_needed} roundtrips)")
    print(f"NEW (Index Set):    {duration_set * 1000:.4f} ms")
    print(f"Speedup: {duration_scan / duration_set:.1f}x")

    # Requirement: < 50ms for NEW
    assert duration_set < 0.050, f"New implementation too slow: {duration_set * 1000:.2f}ms"
