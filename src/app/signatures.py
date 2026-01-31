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
    if isinstance(payload, bytes):
        payload_bytes = payload
    else:
        # json_dumps returns bytes | str. We need bytes.
        # If it returns str (unexpected but possible by type hint), encode it.
        dumped = json_dumps(payload)
        if isinstance(dumped, str):
            payload_bytes = dumped.encode("utf-8")
        else:
            payload_bytes = dumped

    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

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
