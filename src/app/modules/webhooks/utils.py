"""Webhook signature utilities.

This module provides utility functions for webhook signature verification,
intended for use by webhook consumers to validate incoming webhook payloads.
"""

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules.webhooks.signer import canonical_bytes_from_raw, is_timestamp_fresh
from src.app.modules.webhooks.signer import (
    verify_signature_raw as signer_verify_signature_raw,
)
from src.app.shared.exceptions import ValidationError

logger = get_logger(__name__)


_LEGACY_SHA256_PREFIX = "sha256="
_HEX_DIGEST_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_legacy_signature_header(signature: str) -> bool:
    """True when a header uses the retired payload-only digest scheme.

    Covers both shapes the old verifier tolerated: the ``sha256=<hex>``
    prefixed form, and a bare unprefixed hex digest. The bare shape must be
    exactly one SHA-256 digest long so that arbitrary malformed segments
    (which the old verifier also swept into the same bucket) are not
    misreported as unmigrated legacy traffic.
    """
    for part in signature.split(","):
        part = part.strip()
        if part.startswith(_LEGACY_SHA256_PREFIX):
            return True
        if len(part) == _HEX_DIGEST_LENGTH and all(c in _HEX_DIGITS for c in part):
            return True
    return False


async def validate_url_ssrf(url: str) -> None:
    """Validate a webhook URL for SSRF protection.

    The HTTPS scheme check is ALWAYS enforced. When
    WEBHOOK_ALLOW_INTERNAL_URLS is True (non-default), only the
    private-IP/hostname checks are skipped.

    Raises ValidationError if the URL fails validation.
    """
    try:
        await validate_webhook_url(url, allow_internal=settings.app.WEBHOOK_ALLOW_INTERNAL_URLS)
    except SSRFProtectionError as e:
        raise ValidationError(f"Invalid URL: {str(e)}")


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: int,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify a webhook signature for consumers.

    Accepts ONLY the timestamp-bound scheme the delivery path produces:
    ``t=<unix>,v1=<hmac-sha256(secret, b"<t>.<canonical payload>")>``.

    The legacy payload-only ``sha256=<hex>`` form is REJECTED. Its digest
    covers only the payload, so the unsigned ``X-Webhook-Timestamp`` header
    was the sole replay guard: a captured body plus its signature replayed
    indefinitely by refreshing that header. No in-repo consumer ever sent
    the legacy form (delivery.py has always signed ``t=,v1=``), so it was
    retired outright rather than kept alive behind a sunset date.

    Args:
        payload: Raw request body bytes
        signature: X-Webhook-Signature header value (``t={unix},v1={hmac}``)
        timestamp: X-Webhook-Timestamp header value (Unix timestamp)
        secret: Shared signing secret (from endpoint configuration)
        tolerance: Max age in seconds (default 5 min for replay protection)

    Returns:
        True only if the header carries a valid timestamp-bound signature
        and both the supplied timestamp and the signed ``t=`` are fresh.

    Example:
        valid = verify_webhook_signature(
            payload=request.body,
            signature=request.headers['X-Webhook-Signature'],
            timestamp=int(request.headers['X-Webhook-Timestamp']),
            secret=your_signing_secret,
        )
    """
    # Freshness + canonicalization are signer-owned primitives: the signed
    # scheme delegates entirely to signer.verify_signature_raw (the adapter
    # pattern) — dict, list-valued, and non-JSON raw bodies all verify through
    # that single implementation, including rotation headers with multiple
    # v1= segments. This module carries zero local HMAC.
    #
    # The freshness pre-check rejects a stale UNSIGNED header stamp before we
    # spend an HMAC. It is not the replay guard: the signer independently
    # enforces freshness against the `t=` it actually authenticates, which
    # is the value that cannot be swapped.
    if not is_timestamp_fresh(timestamp, tolerance):
        return False

    if not signer_verify_signature_raw(
        canonical_bytes_from_raw(payload), secret, signature, max_age_seconds=tolerance
    ):
        # Surface rejected legacy traffic so an operator can spot a consumer
        # that never migrated off the retired payload-only scheme.
        if _is_legacy_signature_header(signature):
            logger.warning(
                "legacy_webhook_signature_rejected",
                hint="Upgrade the consumer to the t=<unix>,v1=<hmac> scheme",
            )
        return False

    logger.debug("webhook_signature_verified", scheme="t=,v1=")
    return True


def generate_signing_secret() -> str:
    """Generate a cryptographically secure signing secret.

    Returns:
        64-character hex string (256 bits of entropy)
    """
    import secrets

    return secrets.token_hex(32)


__all__ = ["generate_signing_secret", "validate_url_ssrf", "verify_webhook_signature"]
