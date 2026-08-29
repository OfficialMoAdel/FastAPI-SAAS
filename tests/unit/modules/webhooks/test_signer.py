"""R3-01: webhook HMAC golden-vector — locks canonical bytes contract.

Changing ``canonical_payload_bytes`` (e.g. switching to ``orjson``,
dropping ``sort_keys=True``, or reordering) silently invalidates every
consumer's ``v1=`` check without a test failure — this test makes that
contract explicit.
"""

import hashlib
import hmac
import json

from src.app.modules.webhooks.signer import canonical_payload_bytes, sign_payload, verify_signature


def test_canonical_bytes_are_sort_keys_deterministic():
    # insertion order is b,a — canonical must be a,b
    payload = {"b": 2, "a": 1}
    assert canonical_payload_bytes(payload) == b'{"a": 1, "b": 2}'
    # nested
    payload2 = {"z": {"b": 2, "a": 1}, "a": 1}
    # sort_keys sorts top-level keys, nested dict ordering is also sorted by json.dumps
    assert json.loads(canonical_payload_bytes(payload2).decode()) == {"a": 1, "z": {"a": 1, "b": 2}}


def test_sign_payload_golden_vector():
    payload = {"event": "user.created", "user_id": 42}
    secret = "whsec_test_secret"
    ts = 1700000000
    # canonical
    expected_bytes = json.dumps(payload, sort_keys=True).encode()
    assert canonical_payload_bytes(payload) == expected_bytes
    signed = f"{ts}.".encode() + expected_bytes
    expected_digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    expected_header = f"t={ts},v1={expected_digest}"
    assert sign_payload(payload, secret, ts) == expected_header


def test_verify_signature_round_trip():
    payload = {"a": 1}
    secret = "s3cr3t"
    ts = 1234567890
    header = sign_payload(payload, secret, ts)
    assert verify_signature(payload, secret, header) is True
    assert verify_signature(payload, "wrong", header) is False
    assert verify_signature({"a": 2}, secret, header) is False


def test_verify_rejects_malformed_header():
    assert verify_signature({"a": 1}, "s", "not-a-header") is False
    assert verify_signature({"a": 1}, "s", "t=abc,v1=xyz") is False
