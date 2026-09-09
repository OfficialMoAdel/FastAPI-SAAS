"""Webhook signature utilities.

This module provides utility functions for webhook signature verification,
intended for use by webhook consumers to validate incoming webhook payloads.
"""

import hashlib
import hmac
from datetime import UTC, date, datetime

from fastapi import HTTPException, status

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules.webhooks.signer import canonical_bytes_from_raw, is_timestamp_fresh

logger = get_logger(__name__)

# S-M1: legacy `sha256=<hex over payload>` signatures sign ONLY the payload —
# the freshness timestamp header is not covered by the digest, so a captured
# header/body pair replays indefinitely by refreshing the unsigned timestamp.
# Acceptance is DEPRECATED: verified within the sunset window below (with a
# warning per verified request), rejected outright afterwards. New consumers
# must use the producer scheme `t=<unix>,v1=<hmac over b"<t>.<payload>">`
_LEGACY_SIGNATURE_SUNSET_DATE = date(2027, 1, 1)


def _legacy_signature_sunset_reached() -> bool:
    """True once the legacy-signature acceptance window has closed (S-M1)."""
    return datetime.now(UTC).date() >= _LEGACY_SIGNATURE_SUNSET_DATE


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
        signature: X-Webhook-Signature header value (preferred: t={unix},v1={hmac}; deprecated legacy: sha256={hex})
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
    # bare `sha256=<hex over payload>` format is only accepted during the
    # S-M1 sunset window (see _LEGACY_SIGNATURE_SUNSET_DATE); its replay
    # protection relies solely on the header timestamp freshness check below.
    # Freshness + canonicalization are signer-owned primitives (candidate 5):
    # ONE implementation of the replay window and the canonical-bytes
    # contract shared by producer (signer.sign_payload) and this consumer
    # verifier.
    if not is_timestamp_fresh(timestamp, tolerance):
        return False

    payload_bytes = canonical_bytes_from_raw(payload)

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

    # Scheme 1 (preferred): timestamp-bound signature — the HMAC covers the
    # timestamp, so a captured pair cannot be replayed with a refreshed stamp.
    signed_digests = [d for fmt, d in candidates if fmt == "signed"]
    if signed_digests and signed_ts is not None:
        if not is_timestamp_fresh(signed_ts, tolerance):
            return False
        expected_signed = _hmac_hex(f"{signed_ts}.".encode() + payload_bytes)
        for digest in signed_digests:
            if hmac.compare_digest(digest, expected_signed):
                return True

    # Scheme 2 (legacy fallback, DEPRECATED — S-M1): payload-only signature.
    # The digest covers only the payload, so its replay protection relies on
    # the header-timestamp freshness check at the top. Acceptance is gated on
    # a sunset date and logs a warning per verified request so operators can
    # find remaining legacy consumers before the hard cutover. Producers
    # already send the timestamp-bound `t=,v1=` scheme (delivery.py), so any
    # legacy acceptance here is a consumer-side compatibility window only.
    legacy_candidates = [d for fmt, d in candidates if fmt in ("legacy", "bare")]
    if legacy_candidates:
        if _legacy_signature_sunset_reached():
            logger.warning(
                "legacy_webhook_signature_rejected_after_sunset",
                sunset_date=_LEGACY_SIGNATURE_SUNSET_DATE.isoformat(),
            )
            return False
        expected_legacy = _hmac_hex(payload_bytes)
        for digest in legacy_candidates:
            if hmac.compare_digest(digest, expected_legacy):
                logger.warning(
                    "legacy_webhook_signature_accepted_deprecated",
                    sunset_date=_LEGACY_SIGNATURE_SUNSET_DATE.isoformat(),
                    hint="Upgrade the consumer to the t=<unix>,v1=<hmac> scheme",
                )
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
