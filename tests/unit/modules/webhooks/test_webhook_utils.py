"""Unit tests for webhook signature utilities.

Tests cover:
- verify_webhook_signature() with replay protection
- generate_signing_secret() randomness
- Timestamp tolerance validation
- Signature format handling
"""

import json
import time
from unittest.mock import patch

from src.app.modules.webhooks.utils import generate_signing_secret, verify_webhook_signature


class TestVerifyWebhookSignature:
    """Tests for webhook signature verification utility."""

    def test_verify_valid_signature(self) -> None:
        """Test verifying a valid webhook signature."""
        from src.app.modules.webhooks.signer import sign_payload

        secret = "test_secret_123"
        payload_dict = {"user_id": "123", "event": "created"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        signature = sign_payload(payload_dict, secret, timestamp)

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_verify_invalid_signature(self) -> None:
        """Test verifying an invalid webhook signature."""
        secret = "test_secret_123"
        payload = b'{"user_id": "123"}'
        signature = "sha256=invalid_signature"
        timestamp = int(time.time())

        result = verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is False

    def test_verify_wrong_secret(self) -> None:
        """Test verifying with wrong secret."""
        secret = "correct_secret"
        wrong_secret = "wrong_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        # Generate signature with correct secret
        import hashlib
        import hmac

        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"
        timestamp = int(time.time())

        # Verify with wrong secret
        result = verify_webhook_signature(payload_bytes, signature, timestamp, wrong_secret)

        assert result is False

    def test_verify_timestamp_replay_protection(self) -> None:
        """Test that old timestamps are rejected (replay protection)."""
        secret = "test_secret"
        payload = b'{"user_id": "123"}'

        # Generate valid signature
        import hashlib
        import hmac

        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"

        # Use old timestamp (6 minutes ago, default tolerance is 5 minutes)
        old_timestamp = int(time.time()) - 360

        result = verify_webhook_signature(payload, signature, old_timestamp, secret)

        assert result is False

    def test_verify_future_timestamp_rejected(self) -> None:
        """Test that future timestamps are rejected."""
        secret = "test_secret"
        payload = b'{"user_id": "123"}'

        # Generate valid signature
        import hashlib
        import hmac

        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"

        # Use future timestamp (6 minutes in the future)
        future_timestamp = int(time.time()) + 360

        result = verify_webhook_signature(payload, signature, future_timestamp, secret)

        assert result is False

    def test_verify_custom_tolerance(self) -> None:
        """Test custom tolerance value."""
        from src.app.modules.webhooks.signer import sign_payload

        secret = "test_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        # Use timestamp 6 minutes ago
        old_timestamp = int(time.time()) - 360
        signature = sign_payload(payload_dict, secret, old_timestamp)

        # With custom tolerance of 10 minutes, should pass
        result = verify_webhook_signature(payload_bytes, signature, old_timestamp, secret, tolerance=600)

        assert result is True

    def test_verify_custom_tolerance_signed_scheme(self) -> None:
        """Test custom tolerance with the timestamp-bound t=,v1= scheme."""
        from src.app.modules.webhooks.signer import sign_payload

        secret = "test_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        # 6 minutes ago: fails at default tolerance, passes at 10 minutes
        old_timestamp = int(time.time()) - 360
        signature = sign_payload(payload_dict, secret, old_timestamp)

        assert verify_webhook_signature(payload_bytes, signature, old_timestamp, secret) is False
        assert verify_webhook_signature(payload_bytes, signature, old_timestamp, secret, tolerance=600) is True

    def test_verify_signature_without_prefix(self) -> None:
        """A v1 digest without a t= segment is rejected.

        The timestamp-bound scheme requires both parts of the header; a
        bare v1 digest leaves the timestamp unauthenticated.
        """
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload_bytes, secret, timestamp).split("v1=", 1)[1]

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is False

    def test_verify_signature_with_prefix(self) -> None:
        """Test verifying signature with the t=,v1= scheme."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        payload_dict = {"user_id": "123"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload_bytes, secret, timestamp)

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_verify_payload_key_order_independence(self) -> None:
        """Test that payload key order doesn't affect verification."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        timestamp = int(time.time())

        # Same data, different key order
        payload1 = b'{"a": 1, "b": 2}'
        payload2 = b'{"b": 2, "a": 1}'
        signature = sign_canonical_bytes(payload1, secret, timestamp)

        # Verify against second payload (should work because of JSON re-serialization)
        result = verify_webhook_signature(payload2, signature, timestamp, secret)

        assert result is True

    def test_verify_non_json_payload(self) -> None:
        """Test verifying non-JSON payload uses raw bytes."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        payload = b"raw bytes payload"
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload, secret, timestamp)

        result = verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is True

    def test_verify_empty_payload(self) -> None:
        """Test verifying empty payload."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        payload = b""
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload, secret, timestamp)

        result = verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is True

    def test_verify_unicode_payload(self) -> None:
        """Test verifying payload with unicode characters."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = "test_secret"
        payload_dict = {"message": "Hello 世界 🌍"}
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload_bytes, secret, timestamp)

        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_verify_timing_safe_comparison(self) -> None:
        """Test that timing-safe comparison is used (no early exit on mismatch)."""
        secret = "test_secret"
        payload = b'{"user_id": "123"}'

        # Generate valid signature
        import hashlib
        import hmac

        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

        # Create signature with single character difference
        invalid_sig = f"sha256={expected_sig[:-1]}0"
        timestamp = int(time.time())

        result = verify_webhook_signature(payload, invalid_sig, timestamp, secret)

        assert result is False

    def test_legacy_signature_rejected_with_migration_warning(self) -> None:
        """S-M1: a retired sha256= digest is refused and flagged for operators."""
        import hashlib
        import hmac

        from src.app.modules.webhooks import utils as webhooks_utils

        secret = "test_secret"
        payload = b'{"user_id": "123"}'
        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_sig}"
        timestamp = int(time.time())

        with patch("src.app.modules.webhooks.utils.logger") as mock_logger:
            result = webhooks_utils.verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is False
        assert any(
            "legacy_webhook_signature_rejected" in str(call) for call in mock_logger.warning.call_args_list
        )


class TestGenerateSigningSecret:
    """Tests for signing secret generation."""

    def test_generate_secret_length(self) -> None:
        """Test generated secret is 64 characters (256 bits hex encoded)."""
        secret = generate_signing_secret()

        assert len(secret) == 64

    def test_generate_secret_is_hex(self) -> None:
        """Test generated secret contains only hex characters."""
        secret = generate_signing_secret()

        # Should only contain hex characters
        assert all(c in "0123456789abcdef" for c in secret)

    def test_generate_secret_randomness(self) -> None:
        """Test that generated secrets are unique."""
        secrets = [generate_signing_secret() for _ in range(10)]

        # All secrets should be unique
        assert len(set(secrets)) == 10

    def test_generate_secret_entropy(self) -> None:
        """Test that secrets have sufficient entropy (256 bits)."""
        # 64 hex chars = 32 bytes = 256 bits
        secret = generate_signing_secret()

        # Verify it's 32 bytes when decoded
        import binascii

        decoded = binascii.unhexlify(secret)
        assert len(decoded) == 32


class TestWebhookSignatureIntegration:
    """Integration tests for webhook signing and verification."""

    def test_end_to_end_sign_and_verify(self) -> None:
        """Test complete flow: generate secret, sign payload, verify signature."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        # Generate a signing secret
        secret = generate_signing_secret()

        # Create payload
        payload_dict = {
            "event": "user.created",
            "user_id": "12345",
            "email": "test@example.com",
            "timestamp": int(time.time()),
        }
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        signature = sign_canonical_bytes(payload_bytes, secret, timestamp)

        # Verify (simulating what a consumer does)
        result = verify_webhook_signature(payload_bytes, signature, timestamp, secret)

        assert result is True

    def test_tampered_payload_fails_verification(self) -> None:
        """Test that tampered payload fails verification."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = generate_signing_secret()

        # Original payload
        original_payload = b'{"user_id": "123", "amount": 100}'
        timestamp = int(time.time())
        signature = sign_canonical_bytes(original_payload, secret, timestamp)

        # Tampered payload
        tampered_payload = b'{"user_id": "123", "amount": 999}'

        # Verify tampered payload
        result = verify_webhook_signature(tampered_payload, signature, timestamp, secret)

        assert result is False

    def test_replay_attack_protection(self) -> None:
        """Test comprehensive replay attack protection."""
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = generate_signing_secret()
        payload = b'{"user_id": "123"}'

        # Each timestamp scenario needs its own signature: the timestamp is
        # inside the HMAC, so it cannot be swapped after signing.
        current_time = int(time.time())

        # Valid: current time
        sig_now = sign_canonical_bytes(payload, secret, current_time)
        assert verify_webhook_signature(payload, sig_now, current_time, secret) is True

        # Valid: 4 minutes ago (within 5 min tolerance)
        sig_4m = sign_canonical_bytes(payload, secret, current_time - 240)
        assert verify_webhook_signature(payload, sig_4m, current_time - 240, secret) is True

        # Invalid: 6 minutes ago (outside tolerance)
        sig_6m = sign_canonical_bytes(payload, secret, current_time - 360)
        assert verify_webhook_signature(payload, sig_6m, current_time - 360, secret) is False

        # Invalid: 6 minutes in the future
        sig_future = sign_canonical_bytes(payload, secret, current_time + 360)
        assert verify_webhook_signature(payload, sig_future, current_time + 360, secret) is False

    def test_captured_delivery_cannot_be_replayed_with_refreshed_header(self) -> None:
        """A captured modern delivery dies with its signed timestamp.

        This is the attack the retired payload-only scheme allowed: refresh
        the unsigned header stamp and replay. The signed t= still decides.
        """
        from src.app.modules.webhooks.signer import sign_canonical_bytes

        secret = generate_signing_secret()
        payload = b'{"user_id": "123"}'
        captured_at = int(time.time()) - 400
        signature = sign_canonical_bytes(payload, secret, captured_at)

        assert verify_webhook_signature(payload, signature, int(time.time()), secret) is False
