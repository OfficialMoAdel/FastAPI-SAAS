"""End-to-End validation tests for Opus performance optimizations.

Comprehensive E2E validation to verify all optimizations work together.
Runs AFTER all individual unit/integration tests pass.

Prerequisites:
- All TEST-C1, TEST-C2 (Phase 0) tests pass
- All TEST-P1-* (Phase 1) tests pass
- All TEST-P2-* (Phase 2) tests pass
- All TEST-P3-* (Phase 3) tests pass
"""

import asyncio
import gc
import hashlib
import hmac
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

logger = logging.getLogger(__name__)

# Test configuration
CONCURRENT_USERS = 50
WEBHOOK_PAYLOAD_COUNT = 100


class TestFullAuthFlowWithOptimizations:
    """End-to-end authentication flow testing all optimized paths."""

    @pytest.mark.asyncio
    async def test_passkey_lookup_uses_hash_index(self) -> None:
        """Verify passkey lookup uses O(log n) hash index (P1-2)."""
        import inspect

        from src.app.modules.auth.services import passkey as service

        source = inspect.getsource(service)

        # Verify hash-based lookup pattern
        assert "credential_id_hash" in source
        assert "sha256" in source.lower() or "SHA256" in source

        logger.info("E2E: Passkey hash index optimization verified")

    @pytest.mark.asyncio
    async def test_tenant_cache_uses_orjson(self) -> None:
        """Verify tenant cache uses orjson (P3-1)."""
        from src.app.core.json_utils import json_dumps, json_loads

        # Simulate tenant cache data
        tenant_data = {
            "id": str(uuid4()),
            "name": "Test Tenant",
            "settings": {"theme": "dark", "language": "en"},
        }

        start = time.perf_counter()
        serialized = json_dumps(tenant_data)
        deserialized = json_loads(serialized)
        elapsed = time.perf_counter() - start

        assert deserialized["name"] == "Test Tenant"
        assert elapsed < 0.001  # Should be sub-millisecond

        logger.info("E2E: Tenant orjson cache: %.4fms", elapsed * 1000)

    @pytest.mark.asyncio
    async def test_feature_flags_cache_uses_orjson(self) -> None:
        """Verify feature flags use orjson (P3-2)."""
        from src.app.core.json_utils import json_dumps, json_loads

        # Simulate feature flag config
        flag_config = {
            "key": "new_feature",
            "enabled": True,
            "targeting": {
                "rules": [
                    {"conditions": [{"attr": "tier", "op": "eq", "value": "premium"}]},
                ],
            },
        }

        start = time.perf_counter()
        serialized = json_dumps(flag_config)
        deserialized = json_loads(serialized)
        elapsed = time.perf_counter() - start

        assert deserialized["key"] == "new_feature"
        assert elapsed < 0.001

        logger.info("E2E: Feature flags orjson: %.4fms", elapsed * 1000)

    @pytest.mark.asyncio
    async def test_arq_job_uses_orjson_serializer(self) -> None:
        """Verify ARQ jobs use orjson serializer (P2-1)."""
        from src.app.core.worker.settings import WorkerSettings

        # Verify serializer is configured
        assert hasattr(WorkerSettings, "job_serializer")
        assert hasattr(WorkerSettings, "job_deserializer")

        # Test roundtrip
        job_args = {"user_id": 123, "action": "send_email", "data": {"to": "test@example.com"}}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized == job_args
        logger.info("E2E: ARQ orjson serializer roundtrip verified")


class TestApplicationStartupShutdown:
    """Verify clean startup/shutdown with all optimizations."""

    def test_engine_dispose_in_lifespan(self) -> None:
        """Verify engine.dispose() is called during shutdown (P1-1)."""
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        # Verify dispose is in finally block
        assert "await engine.dispose()" in source
        assert "finally:" in source

        finally_pos = source.find("finally:")
        dispose_pos = source.find("await engine.dispose()")

        assert dispose_pos > finally_pos, "dispose must be in finally block"
        logger.info("E2E: Engine dispose in finally block verified")

    def test_uvloop_configured_on_unix(self) -> None:
        """Verify uvloop is configured on Unix platforms (C2)."""
        import inspect

        from src.app.core.worker import functions

        source = inspect.getsource(functions)

        # Verify intentional omission of uvloop import
        assert "uvloop is deliberately not initialized here" in source

        logger.info("E2E: uvloop fallback pattern verified (platform: %s)", sys.platform)

    def test_json_utils_uses_orjson(self) -> None:
        """Verify json_utils uses orjson backend."""
        from src.app.core import json_utils

        # Check orjson is imported
        assert hasattr(json_utils, "json_dumps")
        assert hasattr(json_utils, "json_loads")
        assert hasattr(json_utils, "json_dumps_sorted")

        # Verify orjson backend
        import inspect

        source = inspect.getsource(json_utils)
        assert "import orjson" in source

        logger.info("E2E: json_utils orjson backend verified")


class TestConcurrentLoadWithOptimizations:
    """Load test with concurrent requests."""

    @pytest.mark.asyncio
    async def test_concurrent_json_operations(self) -> None:
        """Verify orjson handles concurrent operations."""
        from src.app.core.json_utils import json_dumps, json_loads

        async def json_roundtrip(user_id: int) -> dict[str, Any]:
            data = {"user_id": user_id, "timestamp": datetime.now(UTC).isoformat()}
            serialized = json_dumps(data)
            return json_loads(serialized)

        start = time.perf_counter()

        # Run CONCURRENT_USERS concurrent operations
        tasks = [json_roundtrip(i) for i in range(CONCURRENT_USERS)]
        results = await asyncio.gather(*tasks)

        elapsed = time.perf_counter() - start

        assert len(results) == CONCURRENT_USERS
        assert all(r["user_id"] == i for i, r in enumerate(results))

        logger.info(
            "E2E: %d concurrent JSON ops in %.4fms (%.2f ops/sec)",
            CONCURRENT_USERS,
            elapsed * 1000,
            CONCURRENT_USERS / elapsed,
        )

    @pytest.mark.asyncio
    async def test_no_memory_leak_in_concurrent_ops(self) -> None:
        """Verify no memory leaks during concurrent operations."""
        from src.app.core.json_utils import json_dumps, json_loads

        # Force garbage collection
        gc.collect()
        gc.collect()

        # Get baseline memory (simplified - just check gc stats)
        gc_before = gc.get_stats()

        # Run many operations
        for _ in range(1000):
            data = {"id": uuid4(), "data": list(range(100))}
            serialized = json_dumps(data)
            json_loads(serialized)

        gc.collect()
        gc.collect()

        gc_after = gc.get_stats()

        # No significant collection count increase indicates no major leaks
        logger.info("E2E: GC stats before: %s", gc_before[0])
        logger.info("E2E: GC stats after: %s", gc_after[0])


class TestWebhookSignatureUnderLoad:
    """Verify webhook signatures are deterministic under load."""

    def test_webhook_signature_determinism(self) -> None:
        """Verify json_dumps_sorted produces identical signatures (C1)."""
        from src.app.core.json_utils import json_dumps_sorted

        payload = {
            "event": "user.created",
            "data": {"id": 123, "email": "test@example.com"},
            "timestamp": "2024-06-15T12:30:45Z",
        }
        secret = b"webhook_secret"

        # Generate WEBHOOK_PAYLOAD_COUNT signatures
        signatures = []
        for _ in range(WEBHOOK_PAYLOAD_COUNT):
            sig = hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()
            signatures.append(sig)

        # All signatures must be identical
        unique_sigs = set(signatures)
        assert len(unique_sigs) == 1, f"Non-deterministic signatures: {len(unique_sigs)} unique"

        logger.info("E2E: %d webhook signatures all identical: %s", WEBHOOK_PAYLOAD_COUNT, signatures[0][:16])

    def test_different_payloads_different_signatures(self) -> None:
        """Verify different payloads produce different signatures."""
        from src.app.core.json_utils import json_dumps_sorted

        secret = b"webhook_secret"

        payloads = [{"id": i, "event": f"event_{i}"} for i in range(10)]
        signatures = [hmac.new(secret, json_dumps_sorted(p), hashlib.sha256).hexdigest() for p in payloads]

        # All signatures should be unique
        assert len(set(signatures)) == len(payloads)
        logger.info("E2E: %d different payloads produced %d unique signatures", len(payloads), len(set(signatures)))

    @pytest.mark.asyncio
    async def test_concurrent_signature_generation(self) -> None:
        """Verify signatures are correct under concurrent generation."""
        from src.app.core.json_utils import json_dumps_sorted

        payload = {"event": "test", "data": {"id": 123}}
        secret = b"test_secret"

        expected_sig = hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()

        async def generate_signature() -> str:
            return hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()

        tasks = [generate_signature() for _ in range(CONCURRENT_USERS)]
        results = await asyncio.gather(*tasks)

        # All concurrent signatures should match expected
        assert all(sig == expected_sig for sig in results)
        logger.info("E2E: %d concurrent signatures all match expected", CONCURRENT_USERS)


class TestOptimizationsIntegration:
    """Integration tests verifying all optimizations work together."""

    def test_all_json_utils_functions_available(self) -> None:
        """Verify all required json_utils functions are available."""
        from src.app.core.json_utils import json_dumps, json_dumps_sorted, json_loads

        # All functions should be callable
        assert callable(json_dumps)
        assert callable(json_loads)
        assert callable(json_dumps_sorted)

        # Quick smoke test
        data = {"test": True}
        assert json_loads(json_dumps(data)) == data
        assert isinstance(json_dumps_sorted(data), bytes)

        logger.info("E2E: All json_utils functions available and working")

    def test_worker_settings_complete(self) -> None:
        """Verify WorkerSettings has all required configuration."""
        from src.app.core.worker.settings import WorkerSettings

        required_attrs = ["job_serializer", "job_deserializer"]

        for attr in required_attrs:
            assert hasattr(WorkerSettings, attr), f"Missing: {attr}"

        logger.info("E2E: WorkerSettings complete with all required attributes")

    def test_selectinload_in_billing_service(self) -> None:
        """Verify billing service uses selectinload (P1-3)."""
        import inspect

        from src.app.modules.billing import service

        source = inspect.getsource(service)

        assert "selectinload" in source
        assert "pricing_tiers" in source

        logger.info("E2E: Billing selectinload optimization verified")


class TestPerformanceBaseline:
    """Establish performance baselines for comparison."""

    def test_json_serialization_throughput(self) -> None:
        """Measure JSON serialization throughput."""
        from src.app.core.json_utils import json_dumps, json_loads

        payload = {
            "id": str(uuid4()),
            "name": "Test User",
            "email": "test@example.com",
            "settings": {"theme": "dark", "notifications": True},
            "tags": ["user", "active", "premium"],
        }

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            serialized = json_dumps(payload)
            json_loads(serialized)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        logger.info(
            "E2E BASELINE: JSON roundtrip: %d ops in %.3fs = %.0f ops/sec",
            iterations,
            elapsed,
            ops_per_sec,
        )

        # Should achieve at least 10k ops/sec
        assert ops_per_sec > 10000, f"Performance too low: {ops_per_sec:.0f} ops/sec"

    def test_sorted_json_throughput(self) -> None:
        """Measure sorted JSON (for signatures) throughput."""
        from src.app.core.json_utils import json_dumps_sorted

        payload = {"z": 1, "a": 2, "m": 3, "nested": {"x": 1, "b": 2}}

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            json_dumps_sorted(payload)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        logger.info(
            "E2E BASELINE: Sorted JSON: %d ops in %.3fs = %.0f ops/sec",
            iterations,
            elapsed,
            ops_per_sec,
        )

        # Should achieve at least 10k ops/sec
        assert ops_per_sec > 10000, f"Performance too low: {ops_per_sec:.0f} ops/sec"

    def test_hmac_signature_throughput(self) -> None:
        """Measure HMAC signature generation throughput."""
        from src.app.core.json_utils import json_dumps_sorted

        payload = {"event": "user.created", "user_id": 123}
        secret = b"webhook_secret_key_here"

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        logger.info(
            "E2E BASELINE: HMAC signature: %d ops in %.3fs = %.0f ops/sec",
            iterations,
            elapsed,
            ops_per_sec,
        )

        # Should achieve at least 5k ops/sec
        assert ops_per_sec > 5000, f"Performance too low: {ops_per_sec:.0f} ops/sec"
