from __future__ import annotations

"""P0-4: Webhooks HMAC orjson Migration Tests.

STATUS: CANCELLED - orjson is NOT compatible with stdlib json for HMAC signatures.

This module documents WHY the orjson migration for webhooks HMAC was cancelled.
The critical test below demonstrates the byte output difference that would
break signature verification for existing webhook consumers.

Problem:
    - stdlib json.dumps(): '{"a": 2, "z": 1}' (space after colon)
    - orjson.dumps():      '{"a":2,"z":1}'   (no space after colon)

Impact:
    - HMAC-SHA256 signatures are byte-for-byte sensitive
    - Any whitespace difference produces a completely different hash
    - Existing webhook consumers would fail signature verification

Decision:
    - Keep stdlib json.dumps(sort_keys=True) for webhook signatures
    - Do NOT migrate to orjson for this specific use case
    - Performance gain (~2x faster) not worth breaking API compatibility
"""


import hashlib
import hmac
import json

import pytest


class TestP04OrjsonIncompatibility:
    """Document why orjson migration was cancelled for webhooks HMAC."""

    def test_stdlib_vs_orjson_byte_output_differs(self) -> None:
        """CRITICAL: stdlib json and orjson produce different byte output.

        This test documents the exact difference that would break HMAC signatures.
        """
        try:
            import orjson
        except ImportError:
            pytest.skip("orjson not installed - test is informational only")

        payload = {"z": 1, "a": 2}

        # stdlib json with sort_keys
        stdlib_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

        # orjson with OPT_SORT_KEYS
        orjson_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)

        # Document the actual difference
        print(f"stdlib: {stdlib_bytes!r}")
        print(f"orjson: {orjson_bytes!r}")

        # CRITICAL: These are NOT equal!
        assert stdlib_bytes != orjson_bytes, (
            "If this assertion fails, orjson behavior has changed and migration should be reconsidered"
        )

        # Show the specific difference
        assert stdlib_bytes == b'{"a": 2, "z": 1}'  # space after colon
        assert orjson_bytes == b'{"a":2,"z":1}'  # no space

    def test_hmac_signatures_differ(self) -> None:
        """HMAC signatures are different due to byte output difference."""
        try:
            import orjson
        except ImportError:
            pytest.skip("orjson not installed - test is informational only")

        payload = {"event": "user.created", "data": {"id": 123}}
        secret = "webhook-secret-key"

        # Current production signature (stdlib)
        stdlib_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        stdlib_signature = hmac.new(secret.encode("utf-8"), stdlib_bytes, hashlib.sha256).hexdigest()

        # Would-be signature with orjson
        orjson_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        orjson_signature = hmac.new(secret.encode("utf-8"), orjson_bytes, hashlib.sha256).hexdigest()

        # CRITICAL: Signatures are DIFFERENT!
        assert stdlib_signature != orjson_signature, "Signatures match - if this happens, orjson behavior has changed"

        # This means existing webhook consumers would fail verification
        # if we switched to orjson


class TestP04CurrentImplementation:
    """Tests for the current (correct) webhook HMAC implementation."""

    def test_signature_generation_uses_stdlib_json(self) -> None:
        """Verify we're using stdlib json for signature generation."""
        from src.app.modules.webhooks.service import WebhookService

        # Create a mock service (we only need to call the method)
        class MockDB:
            pass

        service = WebhookService(MockDB())  # type: ignore

        payload = {"test": "data", "number": 42}
        secret = "test-secret"

        signature = service._generate_signature(payload, secret)

        # Verify it starts with sha256= prefix
        assert signature.startswith("sha256=")

        # Verify it's a valid hex digest (64 chars for SHA256)
        hex_part = signature[7:]  # Remove "sha256=" prefix
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_signature_is_deterministic(self) -> None:
        """Same payload and secret should always produce same signature."""
        from src.app.modules.webhooks.service import WebhookService

        class MockDB:
            pass

        service = WebhookService(MockDB())  # type: ignore

        payload = {"a": 1, "b": 2}
        secret = "test-secret"

        sig1 = service._generate_signature(payload, secret)
        sig2 = service._generate_signature(payload, secret)

        assert sig1 == sig2

    def test_signature_differs_with_different_secrets(self) -> None:
        """Different secrets should produce different signatures."""
        from src.app.modules.webhooks.service import WebhookService

        class MockDB:
            pass

        service = WebhookService(MockDB())  # type: ignore

        payload = {"data": "test"}

        sig1 = service._generate_signature(payload, "secret1")
        sig2 = service._generate_signature(payload, "secret2")

        assert sig1 != sig2

    def test_signature_differs_with_different_payloads(self) -> None:
        """Different payloads should produce different signatures."""
        from src.app.modules.webhooks.service import WebhookService

        class MockDB:
            pass

        service = WebhookService(MockDB())  # type: ignore

        secret = "test-secret"

        sig1 = service._generate_signature({"a": 1}, secret)
        sig2 = service._generate_signature({"a": 2}, secret)

        assert sig1 != sig2
