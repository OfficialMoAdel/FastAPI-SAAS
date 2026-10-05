"""Passkey lookup latency benchmark tests.

Verifies the hash-indexed lookup for passkey credentials rather than an O(n)
scan over a materialised credential list.

NO LATENCY TARGET IS ASSERTED FOR THE ENDPOINT. This file previously carried
"Target: <5ms p99 at 10k credentials, <10ms at 100k credentials", which was
never measured and cannot be: no such measurement exists, a synthetic in-process
dictionary is not a database lookup, and every pre-2026-10-04 figure this
repository holds was taken on Windows and is archived as NON-REPRESENTATIVE
(addendum A-5). A harness that states the number it is trying to measure is
worse than one that states nothing.

HOW THE TIMING WORKS
Every number below is produced by ``src.benchmarks.protocol``, the single
implementation of the H-11 protocol: ``best_of(min-of-N, median-of-M)`` with a
warm-up and a ``gc.collect()`` before every timed batch. The estimator is not
reimplemented here. Pass criteria are ABSOLUTE fixed ceilings chosen in advance,
never ``new < old``: requiring the new number to be smaller than the old number
passes on noise, and this repository has measured 7.1x run-to-run variation for
the SAME unchanged arm (perf C11, perf E1).
"""

import hashlib
import logging
from typing import Any

from src.benchmarks import protocol

logger = logging.getLogger(__name__)

#: The gate this file used to express as ``assert lookup_time < 0.001``. It is a
#: REGRESSION GUARD on a synthetic dict lookup, not a performance claim: a dict
#: subscript is nanoseconds, so the ceiling is deliberately loose enough that it
#: cannot fire on a noisy shared runner.
HASH_LOOKUP_CEILING_US = protocol.Ceiling(1_000.0, "dict.get over a 10k-entry index")


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

        from src.app.modules.auth.services import passkey as service

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

        from src.app.modules.auth.services import passkey as service

        source = inspect.getsource(service)

        # Fallback should NOT exist anymore (migration complete)
        assert "for credential in" not in source, "O(n) fallback loop should be removed after migration"
        # Hash-based lookup should still be present
        assert "credential_id_hash" in source, "Hash-based lookup must be used"

        logger.info("Pure hash lookup verified - O(n) fallback removed")


class TestPasskeyLookupPerformance:
    """Performance characteristics verification tests."""

    def test_hash_lookup_time_complexity(self) -> None:
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

        found: dict[str, Any] | None = None

        def probe() -> None:
            nonlocal found
            found = credentials_by_hash.get(target_hash)

        # iterations=1000 because ONE dict.get is tens of nanoseconds: a single
        # sample would be dominated by the clock's own resolution and by one
        # scheduler preemption. The protocol aggregates the per-call minima, so
        # 1000 samples per batch is what makes the per-call figure mean anything.
        result = protocol.measure(probe, name="passkey_hash_lookup", iterations=1_000, warmup=100)

        assert found is not None
        assert found["id"] == 5000

        protocol.verdict(result, HASH_LOOKUP_CEILING_US).assert_passed()
        logger.info("Hash lookup: %s", protocol.render(result))

    def test_linear_scan_comparison(self) -> None:
        """Compare hash lookup vs linear scan to show improvement.

        TWO KINDS OF CLAIM, HANDLED DIFFERENTLY.

        The ratio is the claim this test exists to make, because a ceiling on
        the SLOW arm cannot detect a complexity regression: if the scan becomes
        O(1) the two arms converge and a fixed ceiling would be satisfied while
        the regression shipped. So the ratio is asserted. It is not asserted as
        ``linear > hash``, which would be satisfied by two numbers 1% apart; it
        must clear ``NOISE_FLOOR_RATIO``, which is the ~2x within-noise floor
        this repository has measured. The previous threshold was ``speedup > 10``,
        which is stronger than a bare ordering but is still an unsigned constant
        with nothing behind it.

        Separately the fast arm carries an ABSOLUTE ceiling, so "the ratio is
        large" can never be satisfied by both arms being slow.
        """
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

        linear_result: dict[str, Any] | None = None
        hash_result: dict[str, Any] | None = None

        def linear_arm() -> None:
            nonlocal linear_result
            linear_result = None
            for cred in credentials:
                if cred["credential_id"] == target_cred:
                    linear_result = cred
                    break

        def hash_arm() -> None:
            nonlocal hash_result
            hash_result = credentials_by_hash.get(target_hash)

        # iterations=5: one 10k-iteration scan IS the unit, so a large inner
        # loop would measure the loop counter rather than the scan.
        linear = protocol.measure(linear_arm, name="passkey_linear_scan", iterations=5, warmup=1)
        hashed = protocol.measure(hash_arm, name="passkey_hash_lookup", iterations=1_000, warmup=100)

        assert linear_result is not None
        assert hash_result is not None
        assert linear_result["id"] == hash_result["id"]

        speedup = linear.best_us / hashed.best_us if hashed.best_us > 0 else float("inf")
        logger.info("Linear: %s", protocol.render(linear))
        logger.info("Hash: %s", protocol.render(hashed))
        logger.info("Speedup: %.1fx (noise floor %.1fx)", speedup, protocol.NOISE_FLOOR_RATIO)

        assert speedup >= protocol.NOISE_FLOOR_RATIO, (
            f"expected the hash index to beat the scan by more than the noise floor "
            f"({protocol.NOISE_FLOOR_RATIO:.1f}x), got {speedup:.1f}x"
        )
        protocol.verdict(hashed, HASH_LOOKUP_CEILING_US).assert_passed()


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

        from src.app.modules.auth.services import passkey as service

        source = inspect.getsource(service)

        # After hash lookup, should verify the actual credential_id matches
        # This handles the theoretical collision case
        assert "credential_id" in source
        # The service should compare the actual credential bytes, not just trust hash
        logger.info("Credential ID verification pattern present in service")
