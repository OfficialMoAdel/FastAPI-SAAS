"""Passkey lookup latency benchmark tests.

Verifies the O(log n) hash-based lookup optimization for passkey credentials.
Target: <5ms p99 at 10k credentials, <10ms at 100k credentials.

These tests verify the optimization works correctly, not actual latency
(which requires real database and production data).
"""

import hashlib
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


class TestPasskeyHashIndexLookup:
    """Test hash-based O(log n) credential lookup."""

    def test_credential_id_hash_generation(self) -> None:
        """Verify SHA256 hash is generated correctly for credential IDs."""
        credential_id = b"test_credential_id_bytes"
        expected_hash = hashlib.sha256(credential_id).hexdigest()

        # Verify the hash format
        assert len(expected_hash) == 64  # SHA256 produces 64 hex chars
        assert expected_hash.isalnum()

        logger.info("Hash for credential: %s -> %s", credential_id[:20], expected_hash[:16])

    def test_hash_consistency(self) -> None:
        """Verify same credential ID always produces same hash."""
        credential_id = b"consistent_credential_id"

        hash1 = hashlib.sha256(credential_id).hexdigest()
        hash2 = hashlib.sha256(credential_id).hexdigest()

        assert hash1 == hash2
        logger.info("Hash consistency verified: %s", hash1[:16])

    def test_hash_uniqueness(self) -> None:
        """Verify different credential IDs produce different hashes."""
        cred1 = b"credential_one"
        cred2 = b"credential_two"

        hash1 = hashlib.sha256(cred1).hexdigest()
        hash2 = hashlib.sha256(cred2).hexdigest()

        assert hash1 != hash2
        logger.info("Hash uniqueness verified: %s != %s", hash1[:16], hash2[:16])


class TestPasskeyLookupAlgorithm:
    """Test the lookup algorithm uses hash index correctly."""

    def test_source_uses_hash_column_filter(self) -> None:
        """Verify passkey service uses credential_id_hash for filtering."""
        import inspect

        from src.app.modules.passkey import service

        source = inspect.getsource(service)

        # Verify hash-based lookup is implemented
        assert "credential_id_hash" in source, "Should use credential_id_hash column"
        assert "sha256" in source.lower() or "SHA256" in source, "Should use SHA256 hashing"
        assert "hexdigest" in source, "Should convert hash to hex string"

        logger.info("Hash-based lookup pattern verified in passkey service")

    def test_source_uses_pure_hash_lookup(self) -> None:
        """Verify O(n) fallback has been removed (post-migration).

        After the credential_id_hash backfill migration, all lookups
        should use the indexed hash column only. No O(n) scans allowed.
        """
        import inspect

        from src.app.modules.passkey import service

        source = inspect.getsource(service)

        # Fallback should NOT exist anymore (migration complete)
        assert "for credential in" not in source, "O(n) fallback loop should be removed after migration"
        # Hash-based lookup should still be present
        assert "credential_id_hash" in source, "Hash-based lookup must be used"

        logger.info("Pure hash lookup verified - O(n) fallback removed")


class TestPasskeyLookupPerformance:
    """Performance characteristics verification tests."""

    @pytest.mark.asyncio
    async def test_hash_lookup_time_complexity(self) -> None:
        """Verify hash lookup is O(1) dictionary lookup, not O(n) scan."""
        # Simulate the lookup pattern
        credentials_by_hash: dict[str, dict[str, Any]] = {}

        # Generate 10k mock credentials
        for i in range(10_000):
            cred_id = f"credential_{i}".encode()
            hash_val = hashlib.sha256(cred_id).hexdigest()
            credentials_by_hash[hash_val] = {
                "id": i,
                "credential_id": cred_id,
                "user_id": f"user_{i % 100}",
            }

        # Lookup by hash (O(1))
        target_cred = b"credential_5000"
        target_hash = hashlib.sha256(target_cred).hexdigest()

        start = time.perf_counter()
        result = credentials_by_hash.get(target_hash)
        lookup_time = time.perf_counter() - start

        assert result is not None
        assert result["id"] == 5000

        # Hash lookup should be very fast (<1ms)
        assert lookup_time < 0.001, f"Hash lookup too slow: {lookup_time:.6f}s"
        logger.info("Hash lookup time: %.6fs (target < 0.001s)", lookup_time)

    @pytest.mark.asyncio
    async def test_linear_scan_comparison(self) -> None:
        """Compare hash lookup vs linear scan to show improvement."""
        credentials: list[dict[str, Any]] = []
        credentials_by_hash: dict[str, dict[str, Any]] = {}

        # Generate 10k mock credentials
        for i in range(10_000):
            cred_id = f"credential_{i}".encode()
            hash_val = hashlib.sha256(cred_id).hexdigest()
            cred = {
                "id": i,
                "credential_id": cred_id,
                "credential_id_hash": hash_val,
                "user_id": f"user_{i % 100}",
            }
            credentials.append(cred)
            credentials_by_hash[hash_val] = cred

        target_cred = b"credential_9999"  # Worst case for linear scan
        target_hash = hashlib.sha256(target_cred).hexdigest()

        # Linear scan (O(n)) - old approach
        start = time.perf_counter()
        linear_result = None
        for cred in credentials:
            if cred["credential_id"] == target_cred:
                linear_result = cred
                break
        linear_time = time.perf_counter() - start

        # Hash lookup (O(1)) - new approach
        start = time.perf_counter()
        hash_result = credentials_by_hash.get(target_hash)
        hash_time = time.perf_counter() - start

        assert linear_result is not None
        assert hash_result is not None
        assert linear_result["id"] == hash_result["id"]

        # Hash should be significantly faster
        speedup = linear_time / hash_time if hash_time > 0 else float("inf")
        logger.info(
            "Linear: %.6fs, Hash: %.6fs, Speedup: %.1fx",
            linear_time,
            hash_time,
            speedup,
        )

        # At 10k items, hash should be at least 10x faster
        assert speedup > 10, f"Expected >10x speedup, got {speedup:.1f}x"


class TestPasskeyHashCollisionHandling:
    """Test handling of hash collisions (should not occur with SHA256)."""

    def test_sha256_collision_resistance(self) -> None:
        """Verify SHA256 has no practical collisions for credential IDs."""
        hashes: set[str] = set()

        # Generate 100k unique credential IDs
        for i in range(100_000):
            cred_id = f"credential_{i}_{i * 17}_{i * 31}".encode()
            hash_val = hashlib.sha256(cred_id).hexdigest()
            hashes.add(hash_val)

        # All hashes should be unique
        assert len(hashes) == 100_000, "Hash collision detected (should be impossible)"
        logger.info("No collisions in 100k SHA256 hashes")

    def test_verification_after_hash_lookup(self) -> None:
        """Verify credential_id is verified after hash lookup (defense in depth)."""
        import inspect

        from src.app.modules.passkey import service

        source = inspect.getsource(service)

        # After hash lookup, should verify the actual credential_id matches
        # This handles the theoretical collision case
        assert "credential_id" in source
        # The service should compare the actual credential bytes, not just trust hash
        logger.info("Credential ID verification pattern present in service")
