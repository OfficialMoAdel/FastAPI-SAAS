"""Performance benchmarks for R1-OPUS optimizations (z28g).

Benchmarks:
1. Bloom Filter: SHA256 vs xxhash3
2. Session Manager: ZSET eviction performance
3. JWT: PEM key pre-parsing
4. Bitmask: O(k) decode algorithm
5. Bitarray: C extension vs bytearray

Run with: uv run pytest tests/benchmarks/bench_r1_opus.py -v -s
"""

import hashlib
import json
import logging
import random
import time
import timeit
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import xxhash
from bitarray import bitarray
from src.app.core.security.bloom_filter import BloomFilter

from src.app.core.rbac.bitmask import PermissionBitmask

logger = logging.getLogger(__name__)

# Results storage
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


class BenchmarkResults:
    """Collect and save benchmark results."""

    def __init__(self):
        self.results: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "python_version": "",
        }

    def add(self, key: str, value: Any):
        self.results[key] = value
        logger.info(f"Benchmark {key}: {value}")

    def save(self):
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filepath = RESULTS_DIR / f"r1_opus_bench_{timestamp}.json"
        with open(filepath, "w") as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Results saved to {filepath}")
        return filepath


@pytest.fixture(scope="module")
def benchmark_results():
    """Fixture to collect all benchmark results."""
    results = BenchmarkResults()
    yield results
    results.save()


class TestBloomFilterHash:
    """Benchmark Bloom Filter hash performance."""

    def test_sha256_vs_xxhash(self, benchmark_results):
        """Compare SHA256 vs xxhash3_128 performance."""
        # Realistic JWT token
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        iterations = 100000

        # Benchmark SHA256
        sha_time = timeit.timeit(lambda: hashlib.sha256(token.encode()).digest(), number=iterations) / iterations * 1e9

        # Benchmark xxhash3_128
        xxh_time = timeit.timeit(lambda: xxhash.xxh3_128(token.encode()).digest(), number=iterations) / iterations * 1e9

        improvement = sha_time / xxh_time

        logger.info(f"SHA256: {sha_time:.1f}ns")
        logger.info(f"xxhash3_128: {xxh_time:.1f}ns")
        logger.info(f"Improvement: {improvement:.1f}x")

        benchmark_results.add("bloom_sha256_ns", round(sha_time, 1))
        benchmark_results.add("bloom_xxhash_ns", round(xxh_time, 1))
        benchmark_results.add("bloom_improvement_x", round(improvement, 1))

        # xxhash should be at least 2x faster (conservative for CI variance)
        assert improvement >= 2, f"Expected 2x+ improvement, got {improvement:.1f}x"

    def test_bloom_might_contain(self, benchmark_results):
        """Benchmark full might_contain operation with xxhash."""
        bf = BloomFilter(expected_items=100000, false_positive_rate=0.01)

        # Add tokens
        tokens = [f"token_{i}_{random.randint(0, 10000)}" for i in range(10000)]
        for t in tokens[:5000]:
            bf.add(t)

        # Benchmark lookup (mix of hits and misses)
        iterations = 50000
        lookup_time = (
            timeit.timeit(lambda: bf.might_contain(random.choice(tokens)), number=iterations) / iterations * 1e9
        )

        logger.info(f"might_contain: {lookup_time:.1f}ns")
        benchmark_results.add("bloom_might_contain_ns", round(lookup_time, 1))

        # Should be reasonably fast (under 10μs, allowing CI variance)
        assert lookup_time < 10000, f"might_contain too slow: {lookup_time}ns"


class TestSessionManagerEviction:
    """Benchmark Session Manager ZSET eviction."""

    @pytest.mark.asyncio
    async def test_eviction_at_limit(self, benchmark_results):
        """Benchmark eviction when at session limit using ZPOPMIN."""
        from src.app.core.security.session_manager import SessionManager

        # Create mock Redis
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        mock_redis.zcard = AsyncMock(return_value=50)  # At limit
        mock_redis.hlen = AsyncMock(return_value=50)
        mock_redis.zpopmin = AsyncMock(return_value=[("oldest_session", 1000.0)])
        mock_redis.hget = AsyncMock(return_value='{"session_id": "old", "created_at": "2024-01-01T00:00:00"}')

        pipe = MagicMock()
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.hdel = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        sm = SessionManager(redis_client=mock_redis, max_sessions=50)

        # Benchmark eviction
        iterations = 100
        timings = []

        for i in range(iterations):
            start = time.perf_counter_ns()
            await sm.register_session(user_id=1, session_id=f"session_{i}", device_info="Test")
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            timings.append(elapsed_us)

        avg_us = sum(timings) / len(timings)
        p95_us = sorted(timings)[int(len(timings) * 0.95)]

        logger.info(f"Eviction avg: {avg_us:.1f}μs, p95: {p95_us:.1f}μs")
        benchmark_results.add("session_evict_avg_us", round(avg_us, 1))
        benchmark_results.add("session_evict_p95_us", round(p95_us, 1))

        # Should be fast (under 500μs with mocks)
        assert avg_us < 500, f"Eviction too slow: {avg_us}μs"


class TestJWTPEMParsing:
    """Benchmark JWT PEM key pre-parsing."""

    def test_pem_cache_effectiveness(self, benchmark_results):
        """Verify cached PEM parsing is faster than repeated parsing."""
        from unittest.mock import patch

        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        # Mock HS256 settings (simpler to test)
        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = None
        mock_settings.SECRET_KEY = MagicMock()
        mock_settings.SECRET_KEY.get_secret_value.return_value = "test_secret_key"

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            # Clear cache
            clear_jwt_key_cache()

            iterations = 10000

            # First call (cache miss)
            start = time.perf_counter_ns()
            _get_parsed_verification_key()
            first_call_ns = time.perf_counter_ns() - start

            # Subsequent calls (cache hits)
            cached_time = timeit.timeit(_get_parsed_verification_key, number=iterations) / iterations * 1e9

            logger.info(f"First call: {first_call_ns}ns")
            logger.info(f"Cached calls: {cached_time:.1f}ns avg")

            benchmark_results.add("jwt_first_call_ns", first_call_ns)
            benchmark_results.add("jwt_cached_call_ns", round(cached_time, 1))

            # Cached should be fast (allowing some CI variance)
            assert cached_time < 500, f"Cached call too slow: {cached_time}ns"


class TestBitmaskDecode:
    """Benchmark Bitmask O(k) decode algorithm."""

    def test_sparse_vs_dense_decode(self, benchmark_results):
        """Compare decode time for sparse vs dense bitmasks."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())

        # Sparse mask (2 bits set)
        sparse_slugs = set(all_slugs[:2])
        sparse_mask = PermissionBitmask.encode(sparse_slugs)

        # Dense mask (all permissions)
        dense_mask = PermissionBitmask.encode(set(all_slugs))

        iterations = 10000

        sparse_time = timeit.timeit(lambda: PermissionBitmask.decode(sparse_mask), number=iterations) / iterations * 1e6

        dense_time = timeit.timeit(lambda: PermissionBitmask.decode(dense_mask), number=iterations) / iterations * 1e6

        ratio = dense_time / sparse_time if sparse_time > 0 else 0

        logger.info(f"Sparse ({len(sparse_slugs)} perms): {sparse_time:.2f}μs")
        logger.info(f"Dense ({len(all_slugs)} perms): {dense_time:.2f}μs")
        logger.info(f"Ratio: {ratio:.2f}x")

        benchmark_results.add("bitmask_sparse_us", round(sparse_time, 2))
        benchmark_results.add("bitmask_dense_us", round(dense_time, 2))
        benchmark_results.add("bitmask_ratio", round(ratio, 2))

        # With O(k) algorithm, dense should take longer than sparse
        assert dense_time > sparse_time, "Dense should take longer with O(k) algorithm"

    def test_popcount_performance(self, benchmark_results):
        """Benchmark bit_count() vs bin().count('1')."""
        mask = 0xFFFFFFFFFFFFFFFF  # 64 bits set
        iterations = 100000

        # Old method: bin().count()
        old_time = timeit.timeit(lambda: bin(mask).count("1"), number=iterations) / iterations * 1e9

        # New method: bit_count()
        new_time = timeit.timeit(lambda: mask.bit_count(), number=iterations) / iterations * 1e9

        improvement = old_time / new_time if new_time > 0 else 0

        logger.info(f"bin().count(): {old_time:.1f}ns")
        logger.info(f"bit_count(): {new_time:.1f}ns")
        logger.info(f"Improvement: {improvement:.1f}x")

        benchmark_results.add("popcount_old_ns", round(old_time, 1))
        benchmark_results.add("popcount_new_ns", round(new_time, 1))
        benchmark_results.add("popcount_improvement_x", round(improvement, 1))

        # bit_count() should be faster
        assert improvement >= 1, f"bit_count() should be faster: {improvement}x"


class TestBitarrayOperations:
    """Benchmark bitarray vs bytearray operations."""

    def test_get_bit_performance(self, benchmark_results):
        """Compare bit access: bytearray vs bitarray."""
        # Setup bytearray (1MB = 8M bits)
        ba = bytearray(125000)

        # Setup bitarray
        bits = bitarray(1000000)
        bits.setall(0)

        iterations = 100000
        pos = 500000

        # bytearray get
        def ba_get():
            byte_idx = pos // 8
            bit_idx = pos % 8
            return bool(ba[byte_idx] & (1 << bit_idx))

        ba_time = timeit.timeit(ba_get, number=iterations) / iterations * 1e9

        # bitarray get
        def bit_get():
            return bool(bits[pos])

        bit_time = timeit.timeit(bit_get, number=iterations) / iterations * 1e9

        improvement = ba_time / bit_time if bit_time > 0 else 0

        logger.info(f"bytearray get: {ba_time:.1f}ns")
        logger.info(f"bitarray get: {bit_time:.1f}ns")
        logger.info(f"Improvement: {improvement:.2f}x")

        benchmark_results.add("bitarray_get_bytearray_ns", round(ba_time, 1))
        benchmark_results.add("bitarray_get_bitarray_ns", round(bit_time, 1))
        benchmark_results.add("bitarray_get_improvement_x", round(improvement, 2))

    def test_count_performance(self, benchmark_results):
        """Compare popcount: manual loop vs bitarray.count()."""
        # Setup bytearray with some bits set
        ba = bytearray(12500)
        for i in range(0, 12500, 3):
            ba[i] = 0xFF

        # Setup bitarray with similar pattern
        bits = bitarray(100000)
        bits.setall(0)
        for i in range(0, 100000, 24):
            for j in range(8):
                if i + j < 100000:
                    bits[i + j] = True

        iterations = 1000

        # Manual popcount
        def ba_count():
            return sum(bin(byte).count("1") for byte in ba)

        ba_time = timeit.timeit(ba_count, number=iterations) / iterations * 1e6

        # bitarray.count()
        def bit_count():
            return bits.count()

        bit_time = timeit.timeit(bit_count, number=iterations) / iterations * 1e6

        improvement = ba_time / bit_time if bit_time > 0 else 0

        logger.info(f"bytearray popcount: {ba_time:.1f}μs")
        logger.info(f"bitarray.count(): {bit_time:.1f}μs")
        logger.info(f"Improvement: {improvement:.1f}x")

        benchmark_results.add("bitarray_count_bytearray_us", round(ba_time, 1))
        benchmark_results.add("bitarray_count_bitarray_us", round(bit_time, 1))
        benchmark_results.add("bitarray_count_improvement_x", round(improvement, 1))

        # bitarray.count() should be faster (C extension)
        assert improvement >= 2, f"Expected 2x+ improvement, got {improvement}x"


class TestBloomFilterIntegration:
    """Benchmark full BloomFilter with all optimizations."""

    def test_full_workflow(self, benchmark_results):
        """Benchmark add and contains with 10k tokens."""
        bf = BloomFilter(expected_items=50000, false_positive_rate=0.01)

        # Generate tokens
        tokens = [f"jwt_token_{i}_{random.randint(0, 999999)}" for i in range(10000)]

        # Benchmark adds
        add_start = time.perf_counter_ns()
        for token in tokens:
            bf.add(token)
        add_total_ms = (time.perf_counter_ns() - add_start) / 1e6
        add_per_token_us = add_total_ms * 1000 / len(tokens)

        # Benchmark lookups
        lookup_start = time.perf_counter_ns()
        for token in tokens:
            bf.might_contain(token)
        lookup_total_ms = (time.perf_counter_ns() - lookup_start) / 1e6
        lookup_per_token_us = lookup_total_ms * 1000 / len(tokens)

        logger.info(f"10k adds: {add_total_ms:.1f}ms ({add_per_token_us:.2f}μs each)")
        logger.info(f"10k lookups: {lookup_total_ms:.1f}ms ({lookup_per_token_us:.2f}μs each)")
        logger.info(f"Fill ratio: {bf.fill_ratio:.4f}")

        benchmark_results.add("bloom_add_per_token_us", round(add_per_token_us, 2))
        benchmark_results.add("bloom_lookup_per_token_us", round(lookup_per_token_us, 2))
        benchmark_results.add("bloom_fill_ratio", round(bf.fill_ratio, 4))

        # Should be fast
        assert add_per_token_us < 10, f"Adds too slow: {add_per_token_us}μs"
        assert lookup_per_token_us < 10, f"Lookups too slow: {lookup_per_token_us}μs"
