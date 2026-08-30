"""R3-01: webhook HMAC golden-vector — locks canonical bytes contract.

Changing ``canonical_payload_bytes`` (e.g. switching to ``orjson``,
dropping ``sort_keys=True``, or reordering) silently invalidates every
consumer's ``v1=`` check without a test failure — this test makes that
contract explicit.
"""

import hashlib
import hmac
import json
import time

from src.app.modules.webhooks.signer import (
    DEFAULT_SIGNATURE_MAX_AGE_SECONDS,
    canonical_payload_bytes,
    sign_payload,
    verify_signature,
)


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
    # P0-1: must use a fresh ts (the window is 300s by default).
    payload = {"a": 1}
    secret = "s3cr3t"
    ts = int(time.time())
    header = sign_payload(payload, secret, ts)
    assert verify_signature(payload, secret, header) is True
    assert verify_signature(payload, "wrong", header) is False
    assert verify_signature({"a": 2}, secret, header) is False


def test_verify_signature_rejects_stale_timestamp():
    # P0-1: a captured header from outside the freshness window must fail.
    payload = {"a": 1}
    secret = "s3cr3t"
    stale_ts = int(time.time()) - (DEFAULT_SIGNATURE_MAX_AGE_SECONDS + 60)
    header = sign_payload(payload, secret, stale_ts)
    assert verify_signature(payload, secret, header) is False


def test_verify_signature_rejects_future_timestamp():
    # P0-1: a future-dated header (clock-skew attack) must fail.
    payload = {"a": 1}
    secret = "s3cr3t"
    future_ts = int(time.time()) + (DEFAULT_SIGNATURE_MAX_AGE_SECONDS + 60)
    header = sign_payload(payload, secret, future_ts)
    assert verify_signature(payload, secret, header) is False


def test_verify_signature_rejects_t_zero():
    # P0-1: t=0 is the trivial replay case — must be rejected.
    payload = {"a": 1}
    assert verify_signature(payload, "s", "t=0,v1=deadbeef") is False


def test_verify_signature_custom_window():
    # P0-1: callers can pass a custom window; a 5-minute-old header passes
    # only when max_age_seconds=600.
    payload = {"a": 1}
    secret = "s3cr3t"
    ts = int(time.time()) - 350
    header = sign_payload(payload, secret, ts)
    assert verify_signature(payload, secret, header, max_age_seconds=300) is False
    assert verify_signature(payload, secret, header, max_age_seconds=600) is True
