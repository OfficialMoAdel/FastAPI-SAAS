"""Webhook signature utilities."""

import hashlib
import hmac
from typing import Any

from src.app.core.json_utils import json_dumps


def generate_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """
    Generate HMAC signature for webhook payload.

    Args:
        payload: Webhook payload data
        secret: Webhook secret

    Returns
    -------
        Hexadecimal signature string
    """
    payload_str = json_dumps(payload, as_str=True)
    signature = hmac.new(secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

    return signature


def verify_webhook_signature(payload: dict[str, Any], secret: str, signature: str) -> bool:
    """
    Verify webhook signature.

    Args:
        payload: Webhook payload data
        secret: Webhook secret
        signature: Received signature

    Returns
    -------
        True if signature is valid
    """
    expected_signature = generate_webhook_signature(payload, secret)

    return hmac.compare_digest(expected_signature, signature)
