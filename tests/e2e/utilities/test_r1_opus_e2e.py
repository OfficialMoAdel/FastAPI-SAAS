"""End-to-End validation tests for R1-OPUS optimizations (z28g.11).

These tests validate the optimizations work correctly in an integrated
environment with mocked Redis/PostgreSQL to simulate real behavior.

Run with: uv run pytest tests/e2e/test_r1_opus_e2e.py -v -s
"""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.security.session_manager import SessionManager

logger = logging.getLogger(__name__)

# Results storage
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


class E2EResults:
    """Collect and save E2E test results."""

    def __init__(self):
        self.results: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tests": {},
        }

    def add(self, test_name: str, status: str, details: dict | None = None):
        self.results["tests"][test_name] = {
            "status": status,
            "details": details or {},
        }
        logger.info(f"E2E {test_name}: {status}")

    def save(self):
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filepath = RESULTS_DIR / f"r1_opus_e2e_{timestamp}.json"
        with open(filepath, "w") as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"E2E results saved to {filepath}")
        return filepath


@pytest.fixture(scope="module")
def e2e_results():
    """Fixture to collect all E2E results."""
    results = E2EResults()
    yield results
    results.save()




class TestSessionManagerIntegration:
    """E2E tests for Session Manager ZSET optimization."""

    @pytest.fixture
    def mock_redis_session(self):  # noqa: C901
        """Create a mock Redis with real-like behavior."""
        # Simulate Redis data structures
        zsets: dict[str, dict[str, float]] = {}  # ZSET: {key: {member: score}}
        hashes: dict[str, dict[str, str]] = {}  # Hash: {key: {field: value}}

        redis = AsyncMock()

        async def hexists(key, field):
            return key in hashes and field in hashes[key]

        async def zcard(key):
            return len(zsets.get(key, {}))

        async def hlen(key):
            return len(hashes.get(key, {}))

        async def zpopmin(key, count=1):
            if key not in zsets or not zsets[key]:
                return []
            # Get min items
            sorted_items = sorted(zsets[key].items(), key=lambda x: x[1])
            result = sorted_items[:count]
            for member, _ in result:
                del zsets[key][member]
            return result

        async def zrange(key, start, end, withscores=False):
            if key not in zsets or not zsets[key]:
                return []
            sorted_items = sorted(zsets[key].items(), key=lambda x: x[1])

            # Redis range is inclusive, Python is exclusive
            if end == -1:
                sliced = sorted_items[start:]
            else:
                sliced = sorted_items[start : end + 1]

            if withscores:
                return sliced
            return [item[0] for item in sliced]

        async def hget(key, field):
            return hashes.get(key, {}).get(field)

        redis.hexists = hexists
        redis.zcard = zcard
        redis.hlen = hlen
        redis.zpopmin = zpopmin
        redis.zrange = zrange
        redis.hget = hget

        # Pipeline mock
        def pipeline():
            pipe = MagicMock()
            pipe_ops = []

            def zadd(key, mapping):
                pipe_ops.append(("zadd", key, mapping))
                return pipe

            def hset(key, field, value):
                pipe_ops.append(("hset", key, field, value))
                return pipe

            def hdel(key, field):
                pipe_ops.append(("hdel", key, field))
                return pipe

            def zrem(key, member):
                pipe_ops.append(("zrem", key, member))
                return pipe

            def delete(key):
                pipe_ops.append(("delete", key))
                return pipe

            def expire(key, ttl):
                pipe_ops.append(("expire", key, ttl))
                return pipe

            async def execute():
                # Execute accumulated operations
                for op in pipe_ops:
                    if op[0] == "zadd":
                        key, mapping = op[1], op[2]
                        if key not in zsets:
                            zsets[key] = {}
                        zsets[key].update(mapping)
                    elif op[0] == "hset":
                        key, field, value = op[1], op[2], op[3]
                        if key not in hashes:
                            hashes[key] = {}
                        hashes[key][field] = value
                    elif op[0] == "hdel":
                        key, field = op[1], op[2]
                        if key in hashes and field in hashes[key]:
                            del hashes[key][field]
                    elif op[0] == "zrem":
                        key, member = op[1], op[2]
                        if key in zsets and member in zsets[key]:
                            del zsets[key][member]
                    elif op[0] == "delete":
                        key = op[1]
                        zsets.pop(key, None)
                        hashes.pop(key, None)
                pipe_ops.clear()
                return [1] * 10

            pipe.zadd = zadd
            pipe.hset = hset
            pipe.hdel = hdel
            pipe.zrem = zrem
            pipe.delete = delete
            pipe.expire = expire
            pipe.execute = execute

            async def aenter(*args):
                return pipe

            async def aexit(*args):
                pass

            pipe.__aenter__ = aenter
            pipe.__aexit__ = aexit
            return pipe

        redis.pipeline = pipeline
        redis._zsets = zsets
        redis._hashes = hashes

        return redis

    @pytest.mark.asyncio
    async def test_session_eviction_latency(self, e2e_results, mock_redis_session):
        """Measure actual eviction latency in simulated environment."""
        sm = SessionManager(redis_client=mock_redis_session, max_sessions=50)
        user_id = 99999

        # Fill to limit
        for i in range(50):
            await sm.register_session(user_id=user_id, session_id=f"s_{i}", device_info="Test")

        # Measure eviction latency
        latencies = []
        for i in range(10):
            start = time.perf_counter()
            await sm.register_session(user_id=user_id, session_id=f"new_{i}", device_info="Test")
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        p95_ms = sorted(latencies)[int(len(latencies) * 0.95)]

        logger.info(f"Eviction latency - avg: {avg_ms:.2f}ms, p95: {p95_ms:.2f}ms")

        e2e_results.add(
            "session_eviction_latency",
            "PASS",
            {
                "avg_ms": round(avg_ms, 2),
                "p95_ms": round(p95_ms, 2),
                "iterations": len(latencies),
            },
        )

        # Should be fast (under 15ms with mocks)
        # Under coverage, timing is not reliable due to instrumentation overhead
        import sys

        if sys.gettrace() is not None:
            pytest.skip("Timing test skipped under coverage instrumentation")
        assert avg_ms < 15.0, f"Eviction too slow: {avg_ms}ms"


class TestBitmaskDecodeIntegration:
    """E2E tests for Bitmask O(k) decode optimization."""

    def test_bitmask_roundtrip(self, e2e_results):
        """Verify bitmask encode/decode roundtrip works correctly."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())

        # Test various subsets
        test_cases = [
            set(all_slugs[:1]),  # Single permission
            set(all_slugs[:5]),  # Few permissions
            set(all_slugs[:10]),  # More permissions
            set(all_slugs),  # All permissions
        ]

        passed = 0
        for i, slugs in enumerate(test_cases):
            mask = PermissionBitmask.encode(slugs)
            decoded = PermissionBitmask.decode(mask)

            if decoded == slugs:
                passed += 1
                logger.info(f"Case {i}: {len(slugs)} perms - PASS")
            else:
                logger.error(f"Case {i}: mismatch - encoded: {slugs}, decoded: {decoded}")

        e2e_results.add(
            "bitmask_roundtrip",
            "PASS" if passed == len(test_cases) else "FAIL",
            {
                "test_cases": len(test_cases),
                "passed": passed,
            },
        )

        assert passed == len(test_cases), f"Failed {len(test_cases) - passed} cases"

    def test_bitmask_decode_performance(self, e2e_results):
        """Measure decode performance with O(k) algorithm."""
        all_slugs = list(PermissionBitmask.get_all_permissions().keys())

        # Sparse mask (2 permissions)
        sparse_slugs = set(all_slugs[:2])
        sparse_mask = PermissionBitmask.encode(sparse_slugs)

        # Dense mask (all permissions)
        dense_mask = PermissionBitmask.encode(set(all_slugs))

        iterations = 1000

        # Time sparse
        start = time.perf_counter()
        for _ in range(iterations):
            PermissionBitmask.decode(sparse_mask)
        sparse_us = (time.perf_counter() - start) * 1e6 / iterations

        # Time dense
        start = time.perf_counter()
        for _ in range(iterations):
            PermissionBitmask.decode(dense_mask)
        dense_us = (time.perf_counter() - start) * 1e6 / iterations

        logger.info(f"Sparse ({len(sparse_slugs)} perms): {sparse_us:.2f}μs")
        logger.info(f"Dense ({len(all_slugs)} perms): {dense_us:.2f}μs")

        e2e_results.add(
            "bitmask_decode_performance",
            "PASS",
            {
                "sparse_perms": len(sparse_slugs),
                "sparse_us": round(sparse_us, 2),
                "dense_perms": len(all_slugs),
                "dense_us": round(dense_us, 2),
                "ratio": round(dense_us / sparse_us, 2) if sparse_us > 0 else 0,
            },
        )


class TestJWTPEMCachingIntegration:
    """E2E tests for JWT PEM key pre-parsing optimization."""

    def test_jwt_cache_functionality(self, e2e_results):
        """Verify JWT key caching works correctly."""

        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        # Mock HS256 settings
        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = None
        mock_settings.JWT_PUBLIC_KEY = None
        mock_settings.SECRET_KEY = MagicMock()
        mock_settings.SECRET_KEY.get_secret_value.return_value = "test_secret"

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            clear_jwt_key_cache()

            # First call
            start = time.perf_counter()
            key1 = _get_parsed_verification_key()
            first_ns = (time.perf_counter() - start) * 1e9

            # Cached calls
            iterations = 10000
            start = time.perf_counter()
            for _ in range(iterations):
                key2 = _get_parsed_verification_key()
            cached_ns = (time.perf_counter() - start) * 1e9 / iterations

            logger.info(f"First call: {first_ns:.1f}ns")
            logger.info(f"Cached calls: {cached_ns:.1f}ns avg")

            e2e_results.add(
                "jwt_cache_functionality",
                "PASS",
                {
                    "first_call_ns": round(first_ns, 1),
                    "cached_call_ns": round(cached_ns, 1),
                    "cache_effective": key1 is key2,
                },
            )

            assert key1 is key2, "Cache should return same object"




class TestFullOptimizationFlow:
    """E2E test combining all optimizations in realistic flow."""

    @pytest.fixture
    def mock_redis_flow(self):  # noqa: C901
        """Create a mock Redis with real-like behavior for flow test."""
        # Simulate Redis data structures
        zsets: dict[str, dict[str, float]] = {}
        hashes: dict[str, dict[str, str]] = {}

        redis = AsyncMock()

        async def hexists(key, field):
            return key in hashes and field in hashes[key]

        async def zcard(key):
            return len(zsets.get(key, {}))

        async def hlen(key):
            return len(hashes.get(key, {}))

        async def zpopmin(key, count=1):
            if key not in zsets or not zsets[key]:
                return []
            sorted_items = sorted(zsets[key].items(), key=lambda x: x[1])
            result = sorted_items[:count]
            for member, _ in result:
                del zsets[key][member]
            return result

        async def hget(key, field):
            return hashes.get(key, {}).get(field)

        async def eval_lua(script, numkeys, *args):
            """Simulate the Lua script for register_session atomically."""
            # Parse args: zset_key, hash_key, max_sessions, session_id, session_data, timestamp, ttl, max_batch
            if len(args) < 6:
                return [b"success", b"0"]

            zset_key_val = args[0]
            hash_key_val = args[1]
            max_sess = int(args[2])
            sess_id = args[3]
            sess_data = args[4]

            # Check if already exists
            if hash_key_val in hashes and sess_id in hashes[hash_key_val]:
                return [b"already_exists"]

            # Count current sessions
            current_count = len(zsets.get(zset_key_val, {}))
            if current_count == 0:
                current_count = len(hashes.get(hash_key_val, {}))

            evicted_data = []
            final_count = current_count + 1

            # Evict oldest if over limit
            if current_count >= max_sess:
                sessions_to_remove = current_count - max_sess + 1
                if zset_key_val in zsets and zsets[zset_key_val]:
                    sorted_sessions = sorted(zsets[zset_key_val].items(), key=lambda x: x[1])
                    for member, _ in sorted_sessions[:sessions_to_remove]:
                        if hash_key_val in hashes and member in hashes[hash_key_val]:
                            evicted_data.append(
                                hashes[hash_key_val][member].encode()
                                if isinstance(hashes[hash_key_val][member], str)
                                else hashes[hash_key_val][member]
                            )
                            del hashes[hash_key_val][member]
                        if member in zsets[zset_key_val]:
                            del zsets[zset_key_val][member]
                final_count = final_count - sessions_to_remove

            # Add new session
            timestamp = float(args[5])
            if zset_key_val not in zsets:
                zsets[zset_key_val] = {}
            zsets[zset_key_val][sess_id] = timestamp
            if hash_key_val not in hashes:
                hashes[hash_key_val] = {}
            hashes[hash_key_val][sess_id] = sess_data

            result = [b"success", str(final_count).encode()]
            result.extend(evicted_data)
            return result

        redis.hexists = hexists
        redis.zcard = zcard
        redis.hlen = hlen
        redis.zpopmin = zpopmin
        redis.hget = hget
        redis.eval = eval_lua
        redis.evalsha = eval_lua

        def pipeline():
            pipe = MagicMock()
            pipe_ops = []

            def zadd(key, mapping):
                pipe_ops.append(("zadd", key, mapping))
                return pipe

            def hset(key, field, value):
                pipe_ops.append(("hset", key, field, value))
                return pipe

            def hdel(key, field):
                pipe_ops.append(("hdel", key, field))
                return pipe

            def zrem(key, member):
                pipe_ops.append(("zrem", key, member))
                return pipe

            def delete(key):
                pipe_ops.append(("delete", key))
                return pipe

            def expire(key, ttl):
                pipe_ops.append(("expire", key, ttl))
                return pipe

            async def execute():
                for op in pipe_ops:
                    if op[0] == "zadd":
                        key, mapping = op[1], op[2]
                        if key not in zsets:
                            zsets[key] = {}
                        zsets[key].update(mapping)
                    elif op[0] == "hset":
                        key, field, value = op[1], op[2], op[3]
                        if key not in hashes:
                            hashes[key] = {}
                        hashes[key][field] = value
                    elif op[0] == "hdel":
                        key, field = op[1], op[2]
                        if key in hashes and field in hashes[key]:
                            del hashes[key][field]
                    elif op[0] == "zrem":
                        key, member = op[1], op[2]
                        if key in zsets and member in zsets[key]:
                            del zsets[key][member]
                    elif op[0] == "delete":
                        key = op[1]
                        zsets.pop(key, None)
                        hashes.pop(key, None)
                pipe_ops.clear()
                return [1] * 10

            pipe.zadd = zadd
            pipe.hset = hset
            pipe.hdel = hdel
            pipe.zrem = zrem
            pipe.delete = delete
            pipe.expire = expire
            pipe.execute = execute

            async def aenter(*args):
                return pipe

            async def aexit(*args):
                pass

            pipe.__aenter__ = aenter
            pipe.__aexit__ = aexit
            return pipe

        redis.pipeline = pipeline
        return redis

    @pytest.mark.asyncio
    async def test_complete_auth_simulation(self, e2e_results, mock_redis_flow):
        """Simulate complete auth flow exercising all optimizations."""
        # Setup components
        sm = SessionManager(redis_client=mock_redis_flow, max_sessions=5)

        user_id = 42
        results = {}

        # 1. Login simulation (creates token, registers session)
        start = time.perf_counter()
        await sm.register_session(user_id=user_id, session_id="sess_1", device_info="Chrome/Windows")
        login_ms = (time.perf_counter() - start) * 1000
        results["login_ms"] = round(login_ms, 2)
        logger.info(f"Login: {login_ms:.2f}ms")

        # 2. Session validation
        start = time.perf_counter()
        is_valid = await sm.is_session_valid(user_id, "sess_1")
        session_check_ms = (time.perf_counter() - start) * 1000
        results["session_check_ms"] = round(session_check_ms, 2)
        logger.info(f"Session check: {session_check_ms:.2f}ms, valid={is_valid}")

        # 3. Permission check (bitmask decode)
        all_perms = list(PermissionBitmask.get_all_permissions().keys())
        user_permissions = set(all_perms[:3]) if len(all_perms) >= 3 else set(all_perms)
        mask = PermissionBitmask.encode(user_permissions)
        start = time.perf_counter()
        decoded = PermissionBitmask.decode(mask)
        decode_ns = (time.perf_counter() - start) * 1e9
        results["decode_ns"] = round(decode_ns, 1)
        logger.info(f"Permission decode: {decode_ns:.1f}ns")

        # 4. Logout simulation (invalidate session)
        start = time.perf_counter()
        await sm.invalidate_session(user_id, "sess_1")
        logout_ms = (time.perf_counter() - start) * 1000
        results["logout_ms"] = round(logout_ms, 2)
        logger.info(f"Logout: {logout_ms:.2f}ms")

        e2e_results.add(
            "complete_auth_simulation",
            "PASS",
            results,
        )

        assert is_valid, "Session should be valid after login"
        assert decoded == user_permissions, "Permissions should decode correctly"
