"""Webhook signature utilities."""

import hashlib
import hmac
import json
from typing import Any


def generate_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """
    Generate HMAC signature for webhook payload.

    Args:
        payload: Webhook payload data
        secret: Webhook secret

    Returns:
        Hexadecimal signature string
    """
    payload_str = json.dumps(payload, separators=(",", ":"))
    signature = hmac.new(secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

    return signature


def verify_webhook_signature(payload: dict[str, Any], secret: str, signature: str) -> bool:
    """
    Verify webhook signature.

    Args:
        payload: Webhook payload data
        secret: Webhook secret
        signature: Received signature

    Returns:
        True if signature is valid
    """
    expected_signature = generate_webhook_signature(payload, secret)

    return hmac.compare_digest(expected_signature, signature)
