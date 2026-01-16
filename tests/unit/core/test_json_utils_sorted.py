"""json_dumps_sorted determinism and correctness tests.

Verifies the json_dumps_sorted() function produces deterministic output
for cryptographic signature generation (webhooks, HMAC, etc.).
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.app.core.json_utils import json_dumps_sorted

logger = logging.getLogger(__name__)


class TestJsonDumpsSortedDeterminism:
    """Test deterministic output across multiple calls."""

    def test_json_dumps_sorted_deterministic(self) -> None:
        """Verify identical output for same input across multiple calls."""
        payload = {"z": 1, "a": 2, "m": 3}

        result1 = json_dumps_sorted(payload)
        result2 = json_dumps_sorted(payload)

        assert result1 == result2
        logger.info("Result 1: %s", result1.hex())
        logger.info("Result 2: %s", result2.hex())

    def test_determinism_across_10_calls(self) -> None:
        """Verify determinism across 10 consecutive calls."""
        payload = {"event": "user.created", "data": {"id": 123, "email": "test@example.com"}}

        results = [json_dumps_sorted(payload) for _ in range(10)]

        # All results should be identical
        assert len(set(results)) == 1, f"Non-deterministic output: {set(results)}"
        logger.info("10 calls produced identical output: %s", results[0][:50])


class TestJsonDumpsSortedKeyOrdering:
    """Test alphabetical key ordering."""

    def test_json_dumps_sorted_key_ordering(self) -> None:
        """Verify keys are alphabetically sorted."""
        payload = {"zebra": 1, "apple": 2}

        result = json_dumps_sorted(payload)

        # 'apple' should come before 'zebra'
        expected = b'{"apple":2,"zebra":1}'
        assert result == expected
        logger.info("Expected: %s", expected)
        logger.info("Actual:   %s", result)

    def test_key_ordering_complex(self) -> None:
        """Verify complex key ordering."""
        payload = {"z": 1, "a": 2, "m": 3, "b": 4, "y": 5}

        result = json_dumps_sorted(payload)
        expected = b'{"a":2,"b":4,"m":3,"y":5,"z":1}'

        assert result == expected
        logger.info("Complex ordering verified: %s", result)


class TestJsonDumpsSortedNestedObjects:
    """Test nested object key ordering."""

    def test_json_dumps_sorted_nested_objects(self) -> None:
        """Verify nested objects also have sorted keys."""
        payload = {"outer": {"z": 1, "a": 2}}

        result = json_dumps_sorted(payload)

        # Inner dict should also be sorted
        decoded = result.decode("utf-8")
        a_pos = decoded.find('"a"')
        z_pos = decoded.find('"z"')

        assert a_pos < z_pos, f"'a' should appear before 'z' in: {decoded}"
        logger.info("Nested structure: %s", result)

    def test_deeply_nested_sorting(self) -> None:
        """Verify deeply nested objects are sorted."""
        payload = {
            "level1": {
                "z_key": {
                    "inner_z": 1,
                    "inner_a": 2,
                },
                "a_key": {"val": 3},
            },
        }

        result = json_dumps_sorted(payload)
        decoded = result.decode("utf-8")

        # a_key should come before z_key
        assert decoded.find("a_key") < decoded.find("z_key")
        # inner_a should come before inner_z
        assert decoded.find("inner_a") < decoded.find("inner_z")

        logger.info("Deep nesting verified: %s", result)


class TestJsonDumpsSortedWebhookSignature:
    """Test webhook signature stability."""

    def test_json_dumps_sorted_webhook_signature_stability(self) -> None:
        """Verify HMAC signatures are identical across calls."""
        payload = {"event": "user.created", "data": {"id": 123, "name": "test"}}
        secret = b"webhook_secret_key"

        sig1 = hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()
        sig2 = hmac.new(secret, json_dumps_sorted(payload), hashlib.sha256).hexdigest()

        assert sig1 == sig2
        logger.info("Signature 1: %s", sig1)
        logger.info("Signature 2: %s", sig2)
        logger.info("Payload: %s", payload)

    def test_signature_different_for_different_payloads(self) -> None:
        """Verify different payloads produce different signatures."""
        secret = b"secret"

        payload1 = {"event": "user.created", "id": 1}
        payload2 = {"event": "user.created", "id": 2}

        sig1 = hmac.new(secret, json_dumps_sorted(payload1), hashlib.sha256).hexdigest()
        sig2 = hmac.new(secret, json_dumps_sorted(payload2), hashlib.sha256).hexdigest()

        assert sig1 != sig2
        logger.info("Different payloads produce different signatures")


class TestJsonDumpsSortedDatetimeHandling:
    """Test datetime serialization."""

    def test_json_dumps_sorted_datetime_handling(self) -> None:
        """Verify datetime objects serialize correctly with UTC Z suffix."""
        dt = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        payload = {"created_at": dt}

        result = json_dumps_sorted(payload)
        decoded = result.decode("utf-8")

        # orjson with OPT_UTC_Z uses 'Z' suffix for UTC
        assert "2024-06-15" in decoded
        assert "12:30:45" in decoded
        assert "Z" in decoded, f"Expected UTC Z suffix in: {decoded}"

        logger.info("Datetime input: %s", dt)
        logger.info("Serialized: %s", result)

    def test_datetime_determinism(self) -> None:
        """Verify datetime serialization is deterministic."""
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        payload = {"ts": dt}

        result1 = json_dumps_sorted(payload)
        result2 = json_dumps_sorted(payload)

        assert result1 == result2
        logger.info("Datetime determinism verified")


class TestJsonDumpsSortedUuidHandling:
    """Test UUID serialization."""

    def test_json_dumps_sorted_uuid_handling(self) -> None:
        """Verify UUID objects serialize correctly as lowercase string."""
        test_uuid = UUID("12345678-1234-5678-1234-567812345678")
        payload = {"id": test_uuid}

        result = json_dumps_sorted(payload)
        decoded = result.decode("utf-8")

        # UUID should be lowercase string
        assert "12345678-1234-5678-1234-567812345678" in decoded
        logger.info("UUID: %s", test_uuid)
        logger.info("Serialized: %s", result)

    def test_uuid_determinism(self) -> None:
        """Verify UUID serialization is deterministic."""
        test_uuid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = {"id": test_uuid}

        result1 = json_dumps_sorted(payload)
        result2 = json_dumps_sorted(payload)

        assert result1 == result2
        logger.info("UUID determinism verified")


class TestJsonDumpsSortedStdlibCompatibility:
    """Test compatibility with stdlib json."""

    def test_json_dumps_sorted_vs_stdlib_compatibility(self) -> None:
        """Compare output structure with stdlib json."""
        payload = {"b": 1, "a": 2}

        orjson_result = json_dumps_sorted(payload)
        stdlib_result = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

        # Parse both and compare structure
        import orjson

        orjson_parsed = orjson.loads(orjson_result)
        stdlib_parsed = json.loads(stdlib_result)

        assert orjson_parsed == stdlib_parsed
        logger.info("orjson:  %s", orjson_result)
        logger.info("stdlib:  %s", stdlib_result)

    def test_stdlib_signature_compatibility(self) -> None:
        """Verify orjson sorted output can be verified by stdlib json consumer."""
        payload = {"z": 1, "a": 2, "m": 3}
        secret = b"shared_secret"

        # Producer uses orjson
        orjson_bytes = json_dumps_sorted(payload)
        producer_sig = hmac.new(secret, orjson_bytes, hashlib.sha256).hexdigest()

        # Consumer recreates with stdlib json (sorted, compact)
        stdlib_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        consumer_sig = hmac.new(secret, stdlib_bytes, hashlib.sha256).hexdigest()

        # Both should produce identical signatures
        assert producer_sig == consumer_sig, f"Signature mismatch:\n  orjson: {orjson_bytes}\n  stdlib: {stdlib_bytes}"
        logger.info("Cross-library signature compatibility verified")


class TestJsonDumpsSortedSpecialTypes:
    """Test special type handling."""

    def test_decimal_handling(self) -> None:
        """Verify Decimal serialization."""
        payload = {"amount": Decimal("123.456")}

        result = json_dumps_sorted(payload)
        decoded = result.decode("utf-8")

        assert "123.456" in decoded
        logger.info("Decimal serialized: %s", result)

    def test_set_handling(self) -> None:
        """Verify set serialization (converted to list)."""
        # Note: sets are unordered, so the JSON output order may vary
        # but the function should not crash
        payload = {"tags": {"a", "b", "c"}}

        result = json_dumps_sorted(payload)
        decoded = result.decode("utf-8")

        assert "tags" in decoded
        logger.info("Set serialized: %s", result)

    def test_mixed_types(self) -> None:
        """Verify mixed type payload."""
        payload = {
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "amount": Decimal("99.99"),
            "created": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "active": True,
            "count": 42,
            "name": "test",
        }

        result1 = json_dumps_sorted(payload)
        result2 = json_dumps_sorted(payload)

        assert result1 == result2
        logger.info("Mixed types determinism verified: %s", result1[:80])
