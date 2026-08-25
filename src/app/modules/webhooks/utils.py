"""Webhook signature utilities.

This module provides utility functions for webhook signature verification,
intended for use by webhook consumers to validate incoming webhook payloads.
"""

import hashlib
import hmac
import json
import time

from fastapi import HTTPException, status

from src.app.core.config import settings
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url


async def validate_url_ssrf(url: str) -> None:
    """Validate a webhook URL for SSRF protection.

    The HTTPS scheme check is ALWAYS enforced. When
    WEBHOOK_ALLOW_INTERNAL_URLS is True (non-default), only the
    private-IP/hostname checks are skipped.

    Raises HTTPException(400) if the URL fails validation.
    """
    try:
        await validate_webhook_url(url, allow_internal=settings.app.WEBHOOK_ALLOW_INTERNAL_URLS)
    except SSRFProtectionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid URL: {str(e)}",
        )


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: int,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify webhook signature for consumers.

    This function is provided as a utility for webhook consumers
    to verify the authenticity of incoming webhooks.

    Args:
        payload: Raw request body bytes
        signature: X-Webhook-Signature header value (format: sha256={hex})
        timestamp: X-Webhook-Timestamp header value (Unix timestamp)
        secret: Shared signing secret (from endpoint configuration)
        tolerance: Max age in seconds (default 5 min for replay protection)

    Returns:
        True if signature is valid and timestamp is fresh

    Raises:
        ValueError: If signature format is invalid

    Example:
        valid = verify_webhook_signature(
            payload=request.body,
            signature=request.headers['X-Webhook-Signature'],
            timestamp=int(request.headers['X-Webhook-Timestamp']),
            secret=your_signing_secret,
        )
    """
    # W2/S-4: prefer the signed-timestamp scheme `t=<unix>,v1=<hmac>` where
    # the HMAC covers b"<t>.<payload>" — this binds the timestamp into the
    # signature so it cannot be stripped/replayed separately. The legacy
    # bare `sha256=<hex over payload>` format is still accepted for old
    # consumers, but its replay protection relies solely on the header
    # timestamp freshness check below.
    if abs(time.time() - timestamp) > tolerance:
        return False

    # Recreate canonical payload bytes (same normalization as the sender)
    try:
        payload_dict = json.loads(payload)
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        # If payload is not valid JSON, use raw bytes
        payload_bytes = payload

    def _hmac_hex(data: bytes) -> str:
        return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()

    # Collect (format, digest) candidates from the comma-separated header
    candidates: list[tuple[str, str]] = []
    signed_ts: int | None = None
    for part in signature.split(","):
        part = part.strip()
        if part.startswith("sha256="):
            candidates.append(("legacy", part[7:]))
        elif part and "=" not in part:
            # Bare hex digest (no prefix) — accepted by the previous verifier.
            candidates.append(("bare", part))
        elif part.startswith("v1="):
            candidates.append(("signed", part[3:]))
        elif part.startswith("t="):
            try:
                signed_ts = int(part[2:])
            except ValueError:
                pass

    # Scheme 1 (preferred): timestamp-bound signature
    signed_digests = [d for fmt, d in candidates if fmt == "signed"]
    if signed_digests and signed_ts is not None:
        if abs(time.time() - signed_ts) > tolerance:
            return False
        expected_signed = _hmac_hex(f"{signed_ts}.".encode() + payload_bytes)
        for digest in signed_digests:
            if hmac.compare_digest(digest, expected_signed):
                return True

    # Scheme 2 (legacy fallback): payload-only signature
    expected_legacy = _hmac_hex(payload_bytes)
    for fmt, digest in candidates:
        if fmt in ("legacy", "bare") and hmac.compare_digest(digest, expected_legacy):
            return True

    return False


def generate_signing_secret() -> str:
    """Generate a cryptographically secure signing secret.

    Returns:
        64-character hex string (256 bits of entropy)
    """
    import secrets

    return secrets.token_hex(32)


__all__ = ["verify_webhook_signature", "generate_signing_secret"]
