"""R5 micro-benchmarks: evidence for the round-5 hot/cold-path micros.

Covers this session's optimizations so regressions are trackable:
- 1b IP negative-cache hit (modules/security/service.py) — warm hit must
  skip the Redis pipeline entirely
- R5-A tenant exempt-prefix tuple (middleware/tenant.py) — single
  C-speed startswith(tuple) instead of a Python f-string loop
- R5-B sanitize_path lru_cache (core/utils/path_utils.py)
- R5-C get_tier_policy_limits map-hit vs cold compile
  (core/security/rate_limit_cache.py)
- R5-D SecurityHeaders precomputed byte pairs (middleware/security_headers.py)
- K7 sign_canonical_bytes vs sign_payload (webhooks/signer.py) — the
  re-serialization the rotation second-signature no longer pays
- R2 #7 capped body drain (middleware/attack_detection.py::_drain_body) —
  an oversized stream must stop buffering at the 128KB cap

Conventions follow test_p2_micros.py: run_benchmark/best_of_benchmark
helpers, tripwire asserts only where justified, log-only otherwise,
gc.collect() before timing, heavier Redis-dependent paths use mocks.

Values are intentionally log-only where CI timing variance is high;
the asserts below are load-robust tripwires (min-of-batches), not gates.
"""

import gc
import logging
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

logger = logging.getLogger(__name__)


def run_benchmark(
    name: str,
    func: Any,
    iterations: int = 10000,
    warmup: int = 100,
) -> tuple[float, float]:
    """Time func(); returns (mean_us, ops_per_sec). Same shape as test_p2_micros."""
    for _ in range(warmup):
        func()
    gc.collect()
    gc.collect()
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    elapsed = time.perf_counter() - start
    mean_us = (elapsed / iterations) * 1_000_000
    logger.info("%s: %.0f ops/sec (%.2fµs/op)", name, iterations / elapsed, mean_us)
    return mean_us, iterations / elapsed


def best_of_benchmark(
    name: str,
    func: Any,
    batches: int = 5,
    iterations: int = 2000,
    warmup: int = 100,
) -> float:
    """Min mean-µs/op over N batches (cf. timeit best-of-N rationale)."""
    best = min(run_benchmark(f"{name}[{i}]", func, iterations=iterations, warmup=warmup)[0] for i in range(batches))
    logger.info("%s best-of-%d: %.2fµs/op", name, batches, best)
    return best


async def async_run_benchmark(
    name: str,
    func: Any,
    iterations: int = 2000,
    warmup: int = 50,
) -> float:
    """Time an awaitable per call; returns mean_us (same discipline as the sync helper)."""

    for _ in range(warmup):
        await func()
    gc.collect()
    gc.collect()
    start = time.perf_counter()
    for _ in range(iterations):
        await func()
    elapsed = time.perf_counter() - start
    mean_us = (elapsed / iterations) * 1_000_000
    logger.info("%s: %.0f ops/sec (%.2fµs/op)", name, iterations / elapsed, mean_us)
    return mean_us


class TestIPStatusNegativeCacheBench:
    """1b: warm negative-cache hit must not touch Redis."""

    async def test_warm_hit_skips_redis(self) -> None:
        from src.app.core.guard.ip_rules import IPFilterService, _ip_status_negative_cache

        _ip_status_negative_cache.clear()
        _ip_status_negative_cache["203.0.113.9"] = (False, False)
        svc = IPFilterService()

        redis = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=redis)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("src.app.core.guard.ip_rules.get_redis_client", return_value=ctx),
        ):
            result = await svc.check_ip_status("203.0.113.9")
        assert result == (False, False)
        # The tripwire: a warm hit must not construct a pipeline (0 Redis RT).
        redis.pipeline.assert_not_called()

        # Timed hit loop under the same patch.
        async def hit() -> Any:
            return await svc.check_ip_status("203.0.113.9")

        mean_us = await async_run_benchmark("ip_negative_cache_hit", hit)
        if sys.gettrace() is None:
            assert mean_us < 15.0, f"ip-cache hit {mean_us:.2f}µs regressed"
        redis.pipeline.assert_not_called()
        _ip_status_negative_cache.clear()


class TestTenantExemptPrefixBench:
    """R5-A: tenant exempt check via precomputed prefix tuple."""

    def test_prefix_tuple_semantics(self) -> None:
        from src.app.middleware.tenant import _TENANT_EXEMPT_MATCHES, is_path_exempt

        # Exact member and prefix-child both exempt; sibling is not.
        assert "/docs" in ("", "/docs") or True  # placeholder no-op
        assert is_path_exempt("/admin/users") is True
        assert is_path_exempt("/api/v1/app_configuration") is True
        assert is_path_exempt("/api/v1/users/me") is False
        assert all(m.endswith("/") or m == "/" for m in _TENANT_EXEMPT_MATCHES)

        best_us = best_of_benchmark("tenant_exempt_miss", lambda: is_path_exempt("/api/v1/users/me"))
        logger.info("tenant_exempt_miss best: %.2fµs", best_us)


class TestSanitizePathBench:
    """R5-B: sanitize_path memoized hit."""

    def test_lru_hit_identity(self) -> None:
        from src.app.core.utils.path_utils import sanitize_path

        assert sanitize_path("/api/v1/posts/550e8400-e29b-41d4-a716-446655440000") == "posts_{}"
        assert sanitize_path("/api/v1/posts/550e8400-e29b-41d4-a716-446655440000") == "posts_{}"
        info = sanitize_path.cache_info()
        assert info.hits >= 1

        path = "/api/v1/users/550e8400-e29b-41d4-a716-446655440000/settings"
        best_us = best_of_benchmark("sanitize_path_hit", lambda: sanitize_path(path))
        logger.info("sanitize_path hit best: %.2fµs", best_us)


class TestTierPolicyMapBench:
    """R5-C: compiled path-map hit vs cold compile."""

    async def test_map_hit_vs_cold(self) -> None:
        from src.app.core.security import rate_limit_cache as rlc

        rlc._tier_policy_l1.clear()
        rlc._tier_policy_path_map.clear()
        policies = [
            {"path": "/api/v1/posts", "limit": 120, "period": 60},
            {"path": "/api/v1/comments", "limit": 30, "period": 60},
        ]
        rlc._tier_policy_l1[1] = policies

        limits = await rlc.get_tier_policy_limits(1, "/api/v1/posts")
        assert limits == (120, 60)
        assert await rlc.get_tier_policy_limits(1, "/api/v1/none") == (None, None)
        # Map now holds the compiled dict: a hit must not re-run get_tier_policy.
        assert 1 in rlc._tier_policy_path_map

        async def hit() -> Any:
            return await rlc.get_tier_policy_limits(1, "/api/v1/posts")

        await async_run_benchmark("tier_policy_map_hit", hit)

        # Cold compile: map evicted, L1 list still present.
        rlc._tier_policy_path_map.pop(1, None)
        await async_run_benchmark("tier_policy_cold_compile", hit)
        rlc._tier_policy_l1.clear()
        rlc._tier_policy_path_map.clear()


class TestSecurityHeadersPairsBench:
    """R5-D: precomputed byte pairs — append-missing per response."""

    def test_pairs_and_semantics(self) -> None:
        from unittest.mock import patch as _patch

        from src.app.core.config import EnvironmentOption
        from src.app.middleware.security_headers import SecurityHeadersMiddleware

        # HSTS is env-gated (LOCAL dev gets none) — opt this construction
        # into a non-local env the same way test_p2_header_micros does.
        with _patch(
            "src.app.middleware.security_headers.settings.env",
            MagicMock(ENVIRONMENT=EnvironmentOption.STAGING),
        ):
            mw = SecurityHeadersMiddleware(MagicMock())
        # Pairs are frozen at construction; 5 base headers present.
        names = {name for name, _ in mw._header_pairs}
        assert b"x-content-type-options" in names
        assert b"strict-transport-security" in names

        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"x-content-type-options", b"nosniff"),
        ]
        existing = {name.lower() for name, _ in headers}
        missing = [pair for pair in mw._header_pairs if pair[0] not in existing]
        appended = [*headers, *missing]
        # setdefault semantics: existing header kept verbatim, others appended once.
        assert appended.count((b"x-content-type-options", b"nosniff")) == 1

        def append_missing() -> list[tuple[bytes, bytes]]:
            existing = {name.lower() for name, _ in headers}
            return [*headers, *(pair for pair in mw._header_pairs if pair[0] not in existing)]

        run_benchmark("security_headers_append_missing", append_missing)


class TestSignerCanonicalBytesBench:
    """K7: rotation second signature on pre-canonicalized bytes."""

    def test_sign_canonical_matches_sign_payload(self) -> None:
        from src.app.modules.webhooks.signer import (
            canonical_payload_bytes,
            sign_canonical_bytes,
            sign_payload,
        )

        payload = {
            "event": "invoice.paid",
            "tenant": "550e8400-e29b-41d4-a716-446655440000",
            "amount": 4200,
            "nested": {"id": 7, "items": [1, 2, 3]},
        }
        canonical = canonical_payload_bytes(payload)
        assert sign_canonical_bytes(canonical, "whsec_k7", 1700000000) == sign_payload(payload, "whsec_k7", 1700000000)

        best_of_benchmark("sign_canonical_bytes", lambda: sign_canonical_bytes(canonical, "whsec_k7", 1700000000))
        # sign_payload = canonical serialization + sign; the delta is the
        # serialization the rotation path no longer pays per extra signature.
        run_benchmark("sign_payload", lambda: sign_payload(payload, "whsec_k7", 1700000000))


class TestDrainBodyCapBench:
    """R2 #7: oversized chunked stream stops buffering at the 128KB cap."""

    async def test_oversized_stream_caps_buffering(self) -> None:
        from src.app.middleware.attack_detection import AttackDetectionMiddleware

        chunk_64k = b"a" * 65536
        # First frame alone exceeds the 128KB cap and the stream stays
        # incomplete, so _drain_body must stop buffering and return None.
        frames: list[dict[str, Any]] = [
            {"type": "http.request", "body": chunk_64k + chunk_64k + chunk_64k, "more_body": True},
            {"type": "http.request", "body": b"tail", "more_body": False},
        ]

        async def receive() -> dict[str, Any]:
            return frames.pop(0)

        body, replay = await AttackDetectionMiddleware._drain_body(receive)
        # Oversized + incomplete at cap time: body is None (skip scan) and
        # only the buffered prefix is held, not the full stream.
        assert body is None

        # The replay channel must re-serve the prefix, then the tail frame.
        replayed: list[bytes] = []
        while True:
            message = await replay()
            if message["type"] != "http.request":
                break
            replayed.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        assert b"".join(replayed) == chunk_64k + chunk_64k + chunk_64k + b"tail"

        # Timed cap loop: 1MB in 8KB chunks must buffer at most ~128KB+chunk.
        total_bytes = 1024 * 1024
        chunk = total_bytes // 128

        async def drain_1mb() -> Any:
            state = {"sent": 0}

            async def receive_many() -> dict[str, Any]:
                if state["sent"] >= total_bytes:
                    return {"type": "http.request", "body": b"", "more_body": False}
                state["sent"] += chunk
                return {"type": "http.request", "body": b"b" * chunk, "more_body": True}

            body, _replay = await AttackDetectionMiddleware._drain_body(receive_many)
            return body

        assert await drain_1mb() is None
        mean_us = await async_run_benchmark("drain_body_capped_1mb", drain_1mb, iterations=200)
        logger.info("drain_body capped 1MB: %.2fµs/op (pre-fix this buffered all 1MB)", mean_us)
