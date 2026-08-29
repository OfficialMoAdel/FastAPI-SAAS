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


def canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON bytes for signing.

    Uses ``sort_keys=True`` so key order is deterministic regardless of how
    the dict was built or JSONB round-tripped. Changing this breaks HMAC.
    """
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def sign_payload(payload: dict, secret: str, timestamp: int) -> str:
    """Return Stripe-style ``t=<ts>,v1=<hex>`` for ``payload``.

    Args:
        payload: Event payload dict as stored (``delivery.payload``).
        secret: Endpoint signing secret (plaintext).
        timestamp: Seconds since epoch placed in the ``t=`` header field.

    The signed message is ``f\"{timestamp}.\".encode() + canonical_bytes``.
    """
    payload_bytes = canonical_payload_bytes(payload)
    signed = f"{timestamp}.".encode() + payload_bytes
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(payload: dict, secret: str, header: str) -> bool:
    """Verify a ``t=...,v1=...`` header produced by :func:`sign_payload`.

    Parses ``t`` from the header and recomputes the expected signature.
    Supports rotation headers with multiple ``v1=`` signatures (e.g.
    ``t=<ts>,v1=<active>,t=<ts>,v1=<old>``) — any match accepts.
    Returns ``False`` on malformed headers.
    """
    try:
        # Collect all v1= candidates (rotation may emit two) — dict would
        # keep only the last, breaking verification against the active secret.
        v1_sigs = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
        t_vals = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("t=")]
        if not v1_sigs or not t_vals:
            return False
        ts = int(t_vals[0])
        expected = sign_payload(payload, secret, ts)
        exp_v1 = expected.split("v1=", 1)[1]
        return any(hmac.compare_digest(exp_v1, c) for c in v1_sigs)
    except Exception:
        return False
