"""S-M1 close-out: the legacy payload-only signature scheme is retired.

The legacy `sha256=<hex over payload>` form signed ONLY the body, so the
unsigned `X-Webhook-Timestamp` header was its entire replay guard. A captured
body plus its signature therefore replayed indefinitely by refreshing that
header, no matter how fresh the stamp looked.

Call-site enumeration found NO in-repo consumer of the legacy form: the only
producer (`webhooks/delivery.py`) has always signed the timestamp-bound
`t=<unix>,v1=<hmac>` scheme, and the Stripe inbound path uses a different
function entirely (`billing/webhook/client.py`). The scheme was therefore
retired outright rather than kept behind a sunset date.

These tests pin the post-cutover contract:
  * the legacy form (prefixed AND bare hex) is rejected, not merely gated
  * the modern form is unchanged, including the producer's rotation header
  * a captured modern delivery cannot be replayed with a refreshed header
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

import pytest

from src.app.modules.webhooks.signer import (
    canonical_bytes_from_raw,
    canonical_payload_bytes,
    is_timestamp_fresh,
    sign_canonical_bytes,
    sign_payload,
)
from src.app.modules.webhooks.utils import verify_webhook_signature

SECRET = "unit_test_signing_secret"
OTHER_SECRET = "a_different_signing_secret"
PAYLOAD = {"amount": 100, "user_id": "123"}
# The exact bytes a consumer sees on the wire: what delivery.py transmits and
# signs are the same object.
WIRE_BYTES = canonical_payload_bytes(PAYLOAD)


def legacy_digest(secret: str, payload: bytes) -> str:
    """Build the retired payload-only digest for a secret/payload pair."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class TestLegacySchemeIsRetired:
    """The payload-only scheme is rejected outright, with no sunset window."""

    def test_prefixed_legacy_header_rejected(self) -> None:
        """A valid `sha256=` digest over the body no longer authenticates."""
        assert (
            verify_webhook_signature(
                WIRE_BYTES, legacy_digest(SECRET, WIRE_BYTES), int(time.time()), SECRET
            )
            is False
        )

    def test_bare_hex_legacy_header_rejected(self) -> None:
        """The unprefixed bare-hex digest is rejected too.

        The old verifier accepted this shape, so it is the form most likely
        to still sit in a non-migrated consumer.
        """
        bare = legacy_digest(SECRET, WIRE_BYTES).removeprefix("sha256=")

        assert verify_webhook_signature(WIRE_BYTES, bare, int(time.time()), SECRET) is False

    def test_legacy_rejected_with_fresh_header_timestamp(self) -> None:
        """Freshness alone never made the legacy form acceptable.

        This is the exact replay the retired scheme permitted: a fresh
        unsigned header stamp is the only input the attacker controls.
        """
        assert (
            verify_webhook_signature(
                WIRE_BYTES, legacy_digest(SECRET, WIRE_BYTES), int(time.time()), SECRET
            )
            is False
        )

    def test_legacy_rejected_within_a_widened_tolerance(self) -> None:
        """Widening the tolerance does not resurrect the retired scheme."""
        assert (
            verify_webhook_signature(
                WIRE_BYTES,
                legacy_digest(SECRET, WIRE_BYTES),
                int(time.time()) - 3600,
                SECRET,
                tolerance=7200,
            )
            is False
        )

    def test_legacy_scheme_symbols_are_gone(self) -> None:
        """The sunset gate and payload-only HMAC path no longer exist.

        A date-gated branch that outlives its window is the retired-key
        pattern; the module must not carry one at all.
        """
        from src.app.modules.webhooks import utils as webhooks_utils

        assert not hasattr(webhooks_utils, "_legacy_signature_sunset_reached")
        assert not hasattr(webhooks_utils, "_LEGACY_SIGNATURE_SUNSET_DATE")

    def test_module_carries_no_payload_only_hmac(self) -> None:
        """utils.py owns no local HMAC; verification delegates to the signer."""
        from src.app.modules.webhooks import utils as webhooks_utils

        assert not hasattr(webhooks_utils, "hmac")
        assert not hasattr(webhooks_utils, "hmac_sha256_hex")


class TestModernSchemeUnchanged:
    """The `t=,v1=` scheme the producer emits must not regress."""

    def test_producer_scheme_accepted(self) -> None:
        """A header from sign_canonical_bytes verifies end to end."""
        timestamp = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)

        assert verify_webhook_signature(WIRE_BYTES, header, timestamp, SECRET) is True

    def test_sign_payload_dict_roundtrip(self) -> None:
        """The dict-based signing helper also verifies against wire bytes."""
        timestamp = int(time.time())
        header = sign_payload(PAYLOAD, SECRET, timestamp)

        assert verify_webhook_signature(WIRE_BYTES, header, timestamp, SECRET) is True

    def test_rotation_header_accepted_under_both_secrets(self) -> None:
        """delivery.py appends `,v1=<old>` during the rotation grace period."""
        timestamp = int(time.time())
        active = sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)
        old_digest = sign_canonical_bytes(WIRE_BYTES, OTHER_SECRET, timestamp).split("v1=", 1)[1]
        rotation_header = f"{active},v1={old_digest}"

        assert verify_webhook_signature(WIRE_BYTES, rotation_header, timestamp, SECRET) is True
        assert verify_webhook_signature(WIRE_BYTES, rotation_header, timestamp, OTHER_SECRET) is True
        assert verify_webhook_signature(WIRE_BYTES, rotation_header, timestamp, "wrong") is False

    def test_wrong_secret_rejected(self) -> None:
        """A valid header under a different secret must not verify."""
        timestamp = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, OTHER_SECRET, timestamp)

        assert verify_webhook_signature(WIRE_BYTES, header, timestamp, SECRET) is False

    def test_tampered_payload_rejected(self) -> None:
        """Mutating the body breaks the signature."""
        timestamp = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)

        assert (
            verify_webhook_signature(
                b'{"amount": 999, "user_id": "123"}', header, timestamp, SECRET
            )
            is False
        )

    def test_tolerance_boundaries_hold(self) -> None:
        """The signed t= is what the freshness window is measured against."""
        now = int(time.time())
        within = sign_canonical_bytes(WIRE_BYTES, SECRET, now - 240)
        beyond = sign_canonical_bytes(WIRE_BYTES, SECRET, now - 360)

        assert verify_webhook_signature(WIRE_BYTES, within, now - 240, SECRET) is True
        assert verify_webhook_signature(WIRE_BYTES, beyond, now - 360, SECRET) is False
        # A widened tolerance re-admits the same body within its window.
        assert verify_webhook_signature(WIRE_BYTES, beyond, now - 360, SECRET, tolerance=600) is True


class TestReplayBound:
    """A captured modern delivery cannot be replayed with a refreshed stamp."""

    def test_refreshed_header_timestamp_does_not_revive_stale_delivery(self) -> None:
        """The attack the legacy scheme allowed fails against `t=,v1=`.

        The attacker captured a delivery, then later refreshes the unsigned
        header stamp to now. The signed t= is untouched and still stale, so
        the signature is refused.
        """
        captured_at = int(time.time()) - 400
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, captured_at)

        assert verify_webhook_signature(WIRE_BYTES, header, int(time.time()), SECRET) is False

    def test_header_timestamp_and_signed_t_must_both_be_fresh(self) -> None:
        """Both timestamps gate verification, and the signed one cannot be moved."""
        now = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, now)

        # Fresh signed t= but a stale header stamp: the pre-check rejects it.
        assert verify_webhook_signature(WIRE_BYTES, header, now - 400, SECRET) is False
        # Both fresh: accepted.
        assert verify_webhook_signature(WIRE_BYTES, header, now, SECRET) is True

    def test_signed_t_cannot_be_retouched_to_extend_the_window(self) -> None:
        """Editing the t= inside the header breaks the digest, not just the check."""
        now = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, now)
        retouched = header.replace(f"t={now},", f"t={now - 400},")

        assert verify_webhook_signature(WIRE_BYTES, retouched, now - 400, SECRET) is False


    def test_signer_freshness_is_independent_of_header(self) -> None:
        """Documents that the signer, not this adapter, is the replay guard."""
        assert is_timestamp_fresh(int(time.time()) - 400) is False
        assert is_timestamp_fresh(int(time.time())) is True


class TestEndToEndDelivery:
    """Counter-test: a real delivery is accepted through the consumer adapter."""

    def test_producer_to_consumer_roundtrip(self) -> None:
        """Serialize as delivery.py does, verify as a consumer does.

        Mirrors the production path: delivery.py canonicalizes the stored
        payload, signs those same bytes with a fresh send timestamp, puts the
        header in X-Webhook-Signature and the stamp in X-Webhook-Timestamp.
        """
        headers = {
            "X-Webhook-Signature": sign_canonical_bytes(WIRE_BYTES, SECRET, int(time.time())),
            "X-Webhook-Timestamp": str(int(time.time())),
        }

        accepted = verify_webhook_signature(
            WIRE_BYTES,
            headers["X-Webhook-Signature"],
            int(headers["X-Webhook-Timestamp"]),
            SECRET,
        )

        assert accepted is True

    def test_canonicalization_preserves_producer_bytes(self) -> None:
        """Canonicalizing the signed bytes is a no-op, so the HMAC still matches."""
        assert canonical_bytes_from_raw(WIRE_BYTES) == WIRE_BYTES

    def test_nested_and_list_payloads_survive_the_adapter(self) -> None:
        """The adapter delegates canonicalization; it does not re-serialize."""
        nested = {"events": [{"z": 1, "a": 2}], "meta": {"n": 1}}
        nested_bytes = canonical_payload_bytes(nested)
        timestamp = int(time.time())
        header = sign_canonical_bytes(nested_bytes, SECRET, timestamp)

        assert verify_webhook_signature(nested_bytes, header, timestamp, SECRET) is True


class TestSchemeLogging:
    """Operators must see which scheme verified, and any legacy attempts."""

    def test_accepted_delivery_logs_the_scheme(self) -> None:
        """A successful verification reports the scheme it used."""
        timestamp = int(time.time())
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)

        with patch("src.app.modules.webhooks.utils.logger") as mock_logger:
            assert verify_webhook_signature(WIRE_BYTES, header, timestamp, SECRET) is True

        verified = [
            call
            for call in mock_logger.debug.call_args_list
            if "webhook_signature_verified" in str(call)
        ]
        assert verified
        assert verified[0].kwargs["scheme"] == "t=,v1="

    def test_rejected_legacy_header_is_flagged(self) -> None:
        """A retired-scheme header is called out for operators to act on."""
        with patch("src.app.modules.webhooks.utils.logger") as mock_logger:
            assert (
                verify_webhook_signature(
                    WIRE_BYTES, legacy_digest(SECRET, WIRE_BYTES), int(time.time()), SECRET
                )
                is False
            )

        assert any(
            "legacy_webhook_signature_rejected" in str(call)
            for call in mock_logger.warning.call_args_list
        )

    def test_malformed_header_does_not_masquerade_as_legacy(self) -> None:
        """Only the retired digest shapes emit the legacy warning."""
        with patch("src.app.modules.webhooks.utils.logger") as mock_logger:
            assert (
                verify_webhook_signature(WIRE_BYTES, "not-a-signature", int(time.time()), SECRET)
                is False
            )

        assert not any(
            "legacy_webhook_signature_rejected" in str(call)
            for call in mock_logger.warning.call_args_list
        )


class TestGoldenVectors:
    """Known-answer vectors pinning the accepted wire format."""

    def test_canonical_bytes_vector(self) -> None:
        """Canonical bytes are the sorted-key stdlib-json encoding."""
        assert WIRE_BYTES == b'{"amount": 100, "user_id": "123"}'
        assert WIRE_BYTES == json.dumps(PAYLOAD, sort_keys=True).encode("utf-8")

    def test_signed_header_vector(self) -> None:
        """The header shape is exactly `t=<unix>,v1=<64 hex>`."""
        timestamp = 1757328000
        header = sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)

        assert header.startswith(f"t={timestamp},v1=")
        digest = header.split("v1=", 1)[1]
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
        # Deterministic: same inputs, same header.
        assert header == sign_canonical_bytes(WIRE_BYTES, SECRET, timestamp)


@pytest.mark.parametrize("secret", [SECRET, OTHER_SECRET])
def test_modern_scheme_is_deterministic_across_signing_sites(secret: str) -> None:
    """sign_payload and sign_canonical_bytes agree for the same inputs."""
    timestamp = int(time.time())
    canonical = canonical_payload_bytes(PAYLOAD)

    assert sign_payload(PAYLOAD, secret, timestamp) == sign_canonical_bytes(canonical, secret, timestamp)
    assert verify_webhook_signature(canonical, sign_payload(PAYLOAD, secret, timestamp), timestamp, secret)
