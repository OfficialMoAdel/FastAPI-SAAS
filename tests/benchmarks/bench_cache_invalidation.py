"""Benchmarks for hierarchical and pattern-based cache invalidation.

Both arms are timed through ``src/benchmarks/protocol`` (H-11), so each number
carries its sample counts, its spread, and a full run stamp. Every gate is an
ABSOLUTE FIXED CEILING. The printed speedup ratio between the OLD and NEW arm is
REPORTED and never asserted: ``new < old`` is not a pass criterion - it passes on
noise, and the measured noise floor here is 7.1x run-to-run on identical code
(perf C11). The ceilings are what decide pass or fail.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils import cache
from src.benchmarks import protocol

# Absolute fixed ceilings for the NEW arm, in microseconds.
HIERARCHICAL_CEILING_US = protocol.Ceiling(10_000.0, "invalidate_tenant_cache(tenant=1, prefix=user)")
PATTERN_CEILING_US = protocol.Ceiling(50_000.0, "invalidate_by_pattern(pattern:*)")


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
    # The full set is not sent over a wire here - the point is to measure the
    # filtering CPU cost, which is what makes the global index expensive.
    all_keys = {f"tenant:1:other:{i}".encode() for i in range(total_keys - target_count)}
    target_keys = {f"tenant:1:user:{i}".encode() for i in range(target_count)}
    all_keys.update(target_keys)

    mock_client.smembers.return_value = all_keys

    async def old_arm() -> None:
        fetched = await mock_client.smembers("tenant_index:1")
        filter_prefix = b"tenant:1:user:"
        filtered = {k for k in fetched if k.startswith(filter_prefix)}
        async with mock_client.pipeline() as pipe:
            pipe.delete(*filtered)
            await pipe.execute()

    old_result = await protocol.measure_async(old_arm, name="invalid_old_global_index", iterations=1, warmup=1)

    # 2. Simulate NEW approach (Sharded Index)
    mock_client.smembers.return_value = target_keys

    async def new_arm() -> None:
        # Logic is inside cache.invalidate_tenant_cache
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache.invalidate_tenant_cache(1, "user")

    new_result = await protocol.measure_async(new_arm, name="invalid_new_sharded_index", iterations=1, warmup=1)

    print(f"OLD (Global Index): {protocol.render(old_result)}")
    print(f"NEW (Sharded Index): {protocol.render(new_result)}")
    print(f"Speedup (REPORTED, never asserted): {old_result.best_us / new_result.best_us:.1f}x")

    protocol.verdict(new_result, HIERARCHICAL_CEILING_US).assert_passed()


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
    match_count = 1_000

    # 1. Simulate SCAN (O(N)). A SCAN over 1M keys needs 1000 roundtrips at
    # count=1000. The keyspace is not materialised: what is being measured is
    # the roundtrip overhead of that loop, which is the whole cost of the
    # pattern-matching approach at this scale.
    scan_calls_needed = 1000
    scan_batches: list[tuple[int, list[str]]] = [
        (i + 1 if i < scan_calls_needed - 1 else 0, []) for i in range(scan_calls_needed)
    ]

    async def scan_arm() -> None:
        # Re-arm the side_effect list on every call. The protocol runs warmup
        # plus repetitions x iterations calls against a shared mock, and a list
        # side_effect raises StopIteration once exhausted, which would abort
        # the run rather than measure it. Re-arming is a single attribute
        # assignment and sits inside the timed window; it is ~100ns against a
        # 1000-roundtrip loop, so it cannot move the number.
        mock_client.scan.side_effect = scan_batches
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache._delete_keys_by_pattern("pattern:*")

    scan_result = await protocol.measure_async(scan_arm, name="invalid_old_scan_1m", iterations=1, warmup=1)

    # 2. Simulate SET (O(1))
    # smembers returns 1000 keys.
    target_keys = {f"pattern:{i}".encode() for i in range(match_count)}
    mock_client.smembers = AsyncMock(return_value=target_keys)
    mock_client.scan.side_effect = None  # Reset

    async def set_arm() -> None:
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache.invalidate_by_pattern("pattern:*")

    set_result = await protocol.measure_async(set_arm, name="invalid_new_index_set", iterations=1, warmup=1)

    print(f"OLD (SCAN 1M keys): {protocol.render(scan_result)} (simulated {scan_calls_needed} roundtrips)")
    print(f"NEW (Index Set):    {protocol.render(set_result)}")
    print(f"Speedup (REPORTED, never asserted): {scan_result.best_us / set_result.best_us:.1f}x")

    protocol.verdict(set_result, PATTERN_CEILING_US).assert_passed()
