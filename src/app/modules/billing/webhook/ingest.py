"""Webhook ingestion entrypoint — transport parsing, verification, and tenant derivation.

Extracted per PR-08 (Finding #11a) to slim billing/router.py while preserving
the H1 security ordering byte-for-byte.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.app.core.config import settings
from src.app.modules.billing.exceptions import WebhookNotConfiguredError
from src.app.modules.billing.webhook.client import verify_webhook_signature
from src.app.modules.billing.webhook.dispatch import handle_webhook_event
from src.app.shared.exceptions import ValidationError

logger = get_logger(__name__)

# Signature-verifier callable: (raw_payload, sig_header, secret) -> verified event.
# May be sync (run in an executor) or async (awaited directly, e.g. in tests).
VerifyFn = Callable[..., Any]


def derive_and_attach_tenant(event: Any) -> None:
    """B6: attach the tenant id parsed from the verified event's metadata.

    ADVISORY-ONLY hint for logging/routing. Handlers MUST re-resolve the
    authoritative tenant from the DB record matched on the verified
    stripe_subscription_id / invoice / charge id — never trust this field
    for authorization or writes.
    """
    obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else {}
    metadata_tenant = obj.get("metadata", {}).get("tenant_id") or obj.get("client_reference_id")
    if metadata_tenant:
        try:
            hint = str(UUID(str(metadata_tenant)))
            event["_derived_tenant_hint"] = hint
            event["derived_tenant_id"] = hint  # back-compat alias, same advisory semantics
        except (ValueError, AttributeError):
            pass


async def ingest_webhook(
    payload: bytes,
    sig_header: str | None,
    verify_fn: VerifyFn | None = None,
) -> dict[str, str]:
    """Ingest, verify, and dispatch a Stripe webhook delivery.

    H1 FIX: the signature is ALWAYS verified against the global platform
    secret first. The tenant identity is derived from the *verified* event
    payload afterwards — never from a query param or unverified body metadata.

    P1-8 NOTE: verification is GLOBAL-only by design. The platform exposes a
    single webhook endpoint, so per-tenant endpoint secrets cannot participate:
    the tenant is unknown until after verification, and a global-verify pass
    already authenticates Stripe as the sender. The per-tenant
    ``encrypted_webhook_secret`` column was omitted as unwired dead weight
    (P1-8). Per-tenant endpoint URLs (Connect-style) would be a separate
    feature, not a column.

    verify_fn is sync-only by contract (runs in executor); async functions
    are awaited directly for test convenience.
    """
    if not sig_header:
        raise ValidationError("Missing Stripe-Signature header")

    webhook_secret = (
        settings.billing.STRIPE_WEBHOOK_SECRET.get_secret_value()
        if settings.billing.STRIPE_WEBHOOK_SECRET
        else None
    )
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not set in configuration")
        raise WebhookNotConfiguredError("Webhook secret not configured")

    verifier = verify_fn or verify_webhook_signature

    # Verify signature against the GLOBAL secret (executor: CPU-bound).
    if inspect.iscoroutinefunction(verifier):
        event = await verifier(payload, sig_header, webhook_secret)
    else:
        event = await asyncio.get_running_loop().run_in_executor(
            None, verifier, payload, sig_header, webhook_secret
        )
        if inspect.isawaitable(event):
            event = await event

    # Tenant is now authoritative: extracted from the VERIFIED event object.
    derive_and_attach_tenant(event)

    # Handle event directly so that exceptions propagate and Stripe can retry
    await handle_webhook_event(event)

    return {"status": "success"}


__all__ = ["derive_and_attach_tenant", "ingest_webhook"]
