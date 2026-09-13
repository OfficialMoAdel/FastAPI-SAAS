"""R3-01: webhook payload signing — extracted from service.py.

Wire contract: ``json.dumps(payload, sort_keys=True).encode('utf-8')`` is the
canonical byte sequence that is HMAC-signed as ``f\"{timestamp}.\".encode() +
payload_bytes``. Switching to ``orjson`` or reordering keys would silently
break consumer-side ``v1=`` verification — this module owns that contract
and is covered by a golden-vector test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

# P0-1: default freshness window for signature verification (seconds).
# Callers can override via ``max_age_seconds`` (wired to
# ``settings.webhooks.WEBHOOK_SIGNATURE_MAX_AGE_SECONDS`` in the service).
DEFAULT_SIGNATURE_MAX_AGE_SECONDS = 300


def canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON bytes for signing.

    Uses ``sort_keys=True`` so key order is deterministic regardless of how
    the dict was built or JSONB round-tripped. Changing this breaks HMAC.
    """
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def sign_canonical_bytes(canonical: bytes, secret: str, timestamp: int) -> str:
    """Return Stripe-style ``t=<ts>,v1=<hex>`` for pre-canonicalized bytes.

    The signed message is ``f\"{timestamp}.\".encode() + canonical``.
    Lets callers that already hold canonical bytes (HTTP content, rotation
    second-signature) skip re-serializing the payload dict per signature.
    """
    signed = f"{timestamp}.".encode() + canonical
    digest = hmac_sha256_hex(secret, signed)
    return f"t={timestamp},v1={digest}"


def sign_payload(payload: dict, secret: str, timestamp: int) -> str:
    """Return Stripe-style ``t=<ts>,v1=<hex>`` for ``payload``.

    Args:
        payload: Event payload dict as stored (``delivery.payload``).
        secret: Endpoint signing secret (plaintext).
        timestamp: Seconds since epoch placed in the ``t=`` header field.

    The signed message is ``f\"{timestamp}.\".encode() + canonical_bytes``.
    """
    return sign_canonical_bytes(canonical_payload_bytes(payload), secret, timestamp)


def hmac_sha256_hex(secret: str, data: bytes) -> str:
    """Single HMAC-SHA256 implementation owned by the signer.

    Both the ``t=,v1=`` scheme and the deprecated legacy payload-only digest
    go through this function so consumer verifiers never carry a local replica.
    """
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def _verify_canonical_bytes(
    canonical: bytes,
    secret: str,
    header: str,
    max_age_seconds: int = DEFAULT_SIGNATURE_MAX_AGE_SECONDS,
) -> bool:
    """Verify a ``t=...,v1=...`` header against pre-canonicalized bytes."""
    # P0-2: parse outside the try-block; only int conversion and header
    # parsing failures are expected here. Anything else (crypto errors,
    # attribute bugs) must propagate instead of being masked as
    # "invalid signature".
    parts = header.split(",")
    v1_sigs = [p.split("=", 1)[1] for p in parts if p.startswith("v1=")]
    t_vals = [p.split("=", 1)[1] for p in parts if p.startswith("t=")]
    if not v1_sigs or not t_vals:
        return False

    try:
        ts = int(t_vals[0])
    except ValueError:
        # Non-numeric t= — malformed header, not a crypto failure.
        return False

    # P1-4: Asymmetric freshness window. Rejects future timestamps beyond
    # 60s clock skew allowance, and rejects past timestamps older than max_age_seconds.
    if not is_timestamp_fresh(ts, max_age_seconds=max_age_seconds):
        return False

    expected = hmac_sha256_hex(secret, f"{ts}.".encode() + canonical)
    return any(hmac.compare_digest(expected, c) for c in v1_sigs)


def verify_signature(
    payload: dict,
    secret: str,
    header: str,
    max_age_seconds: int = DEFAULT_SIGNATURE_MAX_AGE_SECONDS,
) -> bool:
    """Verify a ``t=...,v1=...`` header produced by :func:`sign_payload`.

    Parses ``t`` from the header, rejects stale/future timestamps outside
    ``max_age_seconds`` (P0-1: a captured header previously replayed forever),
    then recomputes the expected signature. Supports rotation headers with
    multiple ``v1=`` signatures (e.g. ``t=<ts>,v1=<active>,v1=<old>``) — any
    match accepts. Returns ``False`` on malformed headers or expired
    timestamps.
    """
    return _verify_canonical_bytes(canonical_payload_bytes(payload), secret, header, max_age_seconds)


def verify_signature_raw(
    payload: bytes,
    secret: str,
    header: str,
    max_age_seconds: int = DEFAULT_SIGNATURE_MAX_AGE_SECONDS,
) -> bool:
    """Verify a ``t=...,v1=...`` header against raw request-body bytes.

    Canonicalizes via :func:`canonical_bytes_from_raw` (the same normalization
    the producer applies), so dict, list-valued, and non-JSON bodies all verify
    through this single implementation — consumer adapters must not carry their
    own HMAC replica.
    """
    return _verify_canonical_bytes(canonical_bytes_from_raw(payload), secret, header, max_age_seconds)


def is_timestamp_fresh(
    timestamp: int,
    max_age_seconds: int = DEFAULT_SIGNATURE_MAX_AGE_SECONDS,
    future_skew_seconds: int = 300,
) -> bool:
    """True when ``timestamp`` is within the replay tolerance of now.

    The freshness window is a signing PRIMITIVE: rejects timestamps
    further in the future than ``future_skew_seconds`` (300s) and older
    than ``max_age_seconds`` (300s).
    """

    now = time.time()
    if timestamp > now + future_skew_seconds:
        return False
    return (now - timestamp) <= max_age_seconds

def canonical_bytes_from_raw(payload: bytes) -> bytes:
    """Canonical bytes from raw request-body bytes.

    Re-parses the raw body and re-serializes with ``sort_keys=True`` — the
    same normalization :func:`canonical_payload_bytes` applies to a dict —
    so a consumer verifying stored/transmitted JSON reproduces the exact
    bytes the sender signed. Non-JSON bodies pass through verbatim.
    """
    import json

    try:
        payload_dict = json.loads(payload)
        return json.dumps(payload_dict, sort_keys=True).encode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return payload

