"""Webhook signature utilities.

This module provides utility functions for webhook signature verification,
intended for use by webhook consumers to validate incoming webhook payloads.
"""

import hashlib
import hmac
import json
import time


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
    # Check timestamp freshness (replay protection)
    if abs(time.time() - timestamp) > tolerance:
        return False

    # Recreate expected signature
    # Parse and re-serialize to ensure consistent ordering
    try:
        payload_dict = json.loads(payload)
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        # If payload is not valid JSON, use raw bytes
        payload_bytes = payload

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    # Extract signature from header (format: sha256={sig})
    actual_signature = signature
    if signature.startswith("sha256="):
        actual_signature = signature[7:]

    # Timing-safe comparison
    return hmac.compare_digest(actual_signature, expected)


def generate_signing_secret() -> str:
    """Generate a cryptographically secure signing secret.

    Returns:
        64-character hex string (256 bits of entropy)
    """
    import secrets

    return secrets.token_hex(32)


__all__ = ["verify_webhook_signature", "generate_signing_secret"]
