"""Webhook delivery — the three lifecycles, one module.

This module owns everything about getting a delivery from PENDING to a
terminal state. Three entry points, three distinct lifecycles:

- ``enqueue``       — schedule a delivery job for a known delivery row.
- ``deliver``        — the retry loop for a RUNNING job: serialize+sign at
                       send time, SSRF-validate, HTTP POST with the pinned
                       IP, circuit-breaker tracking, exponential backoff.
- ``recover``        — the safety-net ARQ cron: harvest PENDING rows whose
                       enqueue was LOST (no running job exists), which the
                       in-flight loop can never see.

The dead-delivery invariant (when is a delivery DEAD vs retryable?) and
the retry schedule (WEBHOOK_RETRY_DELAYS) live here and only here.
``WebhookService`` keeps the endpoint-management surface and delegates its
delivery operations to this module; ``recovery.py``'s cron body also lives
here (the standalone module was merged in).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.config import settings
from src.app.core.db.database import get_db_context
from src.app.core.db.rls import bypass_rls
from src.app.core.events import EventPriority
from src.app.core.logger import get_logger
from src.app.core.security.encryption import decrypt_data
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules._base import ModelNotFoundError
from src.app.modules.audit_logs import audit
from src.app.modules.webhooks.http_client import get_webhook_http_client
from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint
from src.app.modules.webhooks.signer import canonical_payload_bytes, sign_payload

if TYPE_CHECKING:
    from arq.connections import ArqRedis

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lock helper (R1-17)
# ---------------------------------------------------------------------------


async def _release_delivery_lock(redis_client: Any, lock_key: str, lock_held: bool, delivery_id: UUID) -> None:
    """Release the webhook delivery lock, logging failures (R1-17).

    A failed delete is not swallowed silently: the lock carries a 60s TTL, so
    the worst case is a delayed redelivery, but operators should see the error.
    """
    if not (lock_held and redis_client is not None):
        return
    try:
        await redis_client.delete(lock_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook_delivery_lock_release_failed",
            delivery_id=str(delivery_id),
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Lifecycle 1: enqueue
# ---------------------------------------------------------------------------


async def enqueue(
    delivery_id: UUID,
    defer_by: int = 0,
    tenant_id: UUID | None = None,
    *,
    arq_pool: ArqRedis | None = None,
) -> None:
    """Enqueue webhook delivery task.

    Args:
        delivery_id: The delivery to enqueue.
        defer_by: Delay in seconds before the job becomes runnable.
        tenant_id: Optional tenant for the RLS context of the worker job.
        arq_pool: Optional pre-resolved ARQ pool (worker path avoids a
            provider round-trip); defaults to the queue provider.
    """
    if arq_pool is not None:
        pool: ArqRedis | None = arq_pool
    else:
        from src.app.core.utils import queue as queue_module

        pool = queue_module.pool
    if pool:
        args: list[str] = [str(delivery_id)]
        if tenant_id is not None:
            args.append(str(tenant_id))
        try:
            if defer_by > 0:
                await pool.enqueue_job("deliver_webhook_task", *args, _defer_by=defer_by)
            else:
                await pool.enqueue_job("deliver_webhook_task", *args)
        except Exception as e:
            logger.error(
                "webhook_enqueue_failed",
                delivery_id=str(delivery_id),
                error=str(e),
            )
    else:
        logger.warning("Redis queue pool not initialized. Cannot enqueue delivery", delivery_id=delivery_id)


def _spawn_enqueue(delivery_id: UUID, defer_by: int, tenant_id: UUID | None) -> None:
    """Fire-and-forget enqueue with the two context fallbacks the original used.

    create_safe_task first (running loop); if no loop, best-effort
    run_coroutine_threadsafe; both failures are logged, never raised —
    the recovery cron is the safety net for a lost enqueue.
    """
    coro = enqueue(delivery_id, defer_by=defer_by, tenant_id=tenant_id)
    try:
        from src.app.core.utils.background_task import create_safe_task

        create_safe_task(coro)
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as loop_err:
            logger.error(
                "webhook_retry_enqueue_failed",
                delivery_id=str(delivery_id),
                error=str(loop_err),
            )


# ---------------------------------------------------------------------------
# Lifecycle 2: deliver (the retry loop)
# ---------------------------------------------------------------------------


async def deliver(db: AsyncSession, delivery_id: UUID, endpoint: WebhookEndpoint | None = None) -> WebhookDelivery:  # noqa: C901
    """Deliver a webhook (called by background task).

    Implements retry logic with exponential backoff and circuit breaker protection.

    CRITICAL: Uses content= instead of json= to prevent httpx double serialization.
    The payload is serialized ONCE with sort_keys=True to match the HMAC signature.
    """

    # P2.17 FIX: serialize delivery by id to prevent double delivery when the
    # same delivery_id is enqueued twice (e.g. a retry scheduled while the
    # original is still in flight). A short-lived Redis lock ensures only one
    # worker performs the HTTP call; others skip and return the current state.
    from src.app.core.redis_provider import get_redis
    from src.app.core.utils.circuit_breaker import CircuitBreakerOpen, webhook_circuit

    _redis = get_redis()
    _lock_key = f"webhook:deliver_lock:{delivery_id}"
    _lock_held = False
    if _redis is not None:
        _lock_held = bool(await _redis.set(_lock_key, "1", nx=True, ex=60))
        if not _lock_held:
            logger.info("Webhook delivery already in progress, skipping", delivery_id=delivery_id)
            existing = await db.get(WebhookDelivery, delivery_id)
            if not existing:
                raise ModelNotFoundError("WebhookDelivery", delivery_id)
            return existing

    # Get delivery record (skips selectinload if endpoint is already pre-resolved)
    if endpoint is None:
        stmt = (
            select(WebhookDelivery)
            .options(selectinload(WebhookDelivery.webhook_endpoint))
            .where(WebhookDelivery.id == delivery_id)
        )
    else:
        stmt = select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)

    result = await db.execute(stmt)
    delivery = result.unique().scalar_one_or_none()
    if not delivery:
        await _release_delivery_lock(_redis, _lock_key, _lock_held, delivery_id)
        raise ModelNotFoundError("WebhookDelivery", delivery_id)

    # Access pre-resolved or eagerly loaded endpoint
    if endpoint is None:
        endpoint = delivery.webhook_endpoint
    if not endpoint:
        raise ModelNotFoundError("WebhookEndpoint", delivery.webhook_endpoint_id)
    # Check circuit breaker before attempting delivery
    if not webhook_circuit.is_closed:
        import time

        current_time = time.time()
        # Clamp at 0: a breaker opened without a recorded _last_failure_time
        # (or clock adjustments) otherwise yields a hugely negative window,
        # scheduling next_retry_at in the past.
        recovery_time = max(0.0, webhook_circuit.recovery_timeout - (current_time - webhook_circuit._last_failure_time))
        logger.warning(
            "Circuit breaker open for webhooks, skipping delivery %s | recovery_in=%.1fs",
            delivery_id,
            recovery_time,
        )
        # W4 FIX: a circuit-open skip is NOT a delivery attempt. Previously
        # attempt_count was incremented here, so ~6 consecutive open ticks
        # exhausted WEBHOOK_RETRY_DELAYS and pushed the delivery to
        # permanent FAILED with zero HTTP attempts ever made. Now we only
        # defer until the breaker recovers, keeping the retry budget intact.
        recovery_delay = max(1, int(recovery_time))
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=recovery_delay)
        delivery.response_body = f"Circuit breaker open. Skipped. Recovery in {recovery_time:.1f}s"
        await db.commit()
        try:
            _spawn_enqueue(delivery_id, recovery_delay, delivery.tenant_id)
        except RuntimeError:
            logger.warning("webhook_circuit_skip_reenqueue_failed", delivery_id=str(delivery_id))
        await _release_delivery_lock(_redis, _lock_key, _lock_held, delivery_id)
        return delivery

    # W1+W2+S-4 FIX: serialize and sign AT SEND TIME, per attempt.
    #
    # Previously the signature was computed at trigger time over a
    # pre-persistence dict while transmitted bytes were re-serialized after
    # a JSONB round-trip - any value/type normalization silently broke
    # consumer-side HMAC. The timestamp header came from delivery.created_at,
    # so retries older than the 300s replay tolerance were always rejected.
    #
    # Now: one serialization of stored payload, signed with a fresh timestamp
    # (Stripe-style `t=...,v1=...`), so sign/send share identical bytes and
    # retries carry a valid stamp.
    import time as _time

    send_timestamp = int(_time.time())
    # R3-01: canonical bytes owned by signer; reused for HTTP content so
    # transmitted bytes == signed bytes (json.dumps sort_keys contract).
    payload_bytes = canonical_payload_bytes(delivery.payload)
    signature_header = sign_payload(delivery.payload, decrypt_data(endpoint.secret), send_timestamp)
    if (
        endpoint.old_signing_secret
        and endpoint.old_secret_expires_at
        and isinstance(endpoint.old_secret_expires_at, datetime)
    ):
        expires_at = endpoint.old_secret_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at > datetime.now(UTC):
            try:
                # P0-5: Stripe rotation format is ONE timestamp with
                # multiple signatures: t=<ts>,v1=<active>,v1=<old>.
                # Appending a full second `t=...,v1=...` pair produced
                # `t=A,v1=X,t=A,v1=Y`, which third-party Stripe-compatible
                # verifiers reject. The in-repo verifier accepts both
                # forms (it collects every v1= segment).
                old_digest = sign_payload(delivery.payload, decrypt_data(endpoint.old_signing_secret), send_timestamp)
                signature_header = f"{signature_header},v1={old_digest.split('v1=', 1)[1]}"
            except Exception as old_sig_err:
                logger.error(
                    "webhook_old_signing_secret_decryption_failed",
                    endpoint_id=str(endpoint.id),
                    error=old_sig_err,
                )

    # Prepare request headers
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature_header,
        "X-Webhook-Event": delivery.event_type,
        "X-Webhook-ID": str(delivery.id),
        "X-Webhook-Timestamp": str(send_timestamp),
    }

    # Validate URL for SSRF protection before making request.
    # W8 FIX: the HTTPS scheme check is always enforced; the
    # WEBHOOK_ALLOW_INTERNAL_URLS kill switch only relaxes the
    # private-IP/hostname checks, it no longer disables validation.
    try:
        validated_ip = await validate_webhook_url(
            endpoint.url,
            allow_internal=settings.app.WEBHOOK_ALLOW_INTERNAL_URLS,
        )  # noqa: E501 - pinned IP, use extensions to prevent DNS rebinding
    except SSRFProtectionError as e:
        logger.error(
            "webhook_ssrf_blocked",
            delivery_id=str(delivery_id),
            endpoint_url=endpoint.url,
            error=str(e),
        )

        await audit(
            action="webhook.ssrf_blocked",
            entity_type="webhook_delivery",
            entity_id=delivery_id,
            tenant_id=endpoint.tenant_id,
            details={"endpoint_url": endpoint.url, "error": str(e)},
            priority=EventPriority.NORMAL,
        )

        delivery.status = WebhookDeliveryStatus.FAILED
        delivery.response_body = f"SSRF protection: {e}"
        await db.commit()
        await _release_delivery_lock(_redis, _lock_key, _lock_held, delivery_id)
        return delivery

    # Extract data needed for HTTP call before releasing DB connection
    endpoint_url = endpoint.url

    # Release DB connection before making HTTP call to avoid holding
    # connection during network I/O (can take 100ms-30s)
    await db.commit()

    # Attempt delivery with circuit breaker tracking (DB connection released)
    response = None
    http_error: Exception | None = None
    circuit_error: Exception | None = None

    try:
        client = await get_webhook_http_client()
        # CRIT-2 FIX: Use content= with pre-serialized bytes instead of json=
        # This ensures the transmitted bytes match what was signed
        response = await client.post(
            endpoint_url,
            content=payload_bytes,
            headers=headers,
            extensions={"resolved_ip": validated_ip},
        )
    except CircuitBreakerOpen as e:
        circuit_error = e
    except Exception as e:
        http_error = e

    # Re-fetch delivery to re-attach to session after HTTP call
    delivery = await db.get(WebhookDelivery, delivery_id)
    if not delivery:
        raise ModelNotFoundError("WebhookDelivery", delivery_id)

    # Process HTTP result
    if response is not None:
        delivery.attempt_count += 1
        delivery.response_status = response.status_code
        delivery.response_body = response.text[:10000]  # Limit to 10KB

        if 200 <= response.status_code < 300:  # HTTP success range
            # Success - record with circuit breaker
            await webhook_circuit.record_success()
            delivery.status = WebhookDeliveryStatus.DELIVERED
            # Note: delivered_at tracked via updated_at timestamp
            logger.info(
                "Webhook delivered successfully: (attempt )",
                delivery_id=delivery_id,
                attempt_count=delivery.attempt_count,
            )
        else:
            # HTTP error - record failure and retry if attempts remain
            await webhook_circuit.record_failure(Exception(f"HTTP {response.status_code}"))

            # SECURITY FIX: Do not retry non-transient 4xx errors (e.g. 400, 422)
            if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                delivery.status = WebhookDeliveryStatus.FAILED
                logger.error(
                    "Webhook failed with permanent 4xx error, skipping retries",
                    delivery_id=delivery_id,
                    status_code=response.status_code,
                )
            else:
                await _handle_failure(db, delivery)

    elif circuit_error is not None:
        # Circuit opened during delivery attempt
        logger.warning("Webhook circuit breaker opened: %s", circuit_error)
        delivery.attempt_count += 1
        delivery.response_body = str(circuit_error)[:1000]
        await _handle_failure(db, delivery, override_delay=getattr(circuit_error, "recovery_in", None))

    elif http_error is not None:
        # Network/timeout error - record failure and retry
        logger.error("Webhook delivery failed", delivery_id=delivery_id, http_error=http_error)
        await webhook_circuit.record_failure(http_error)
        delivery.attempt_count += 1
        delivery.response_body = str(http_error)[:1000]
        await _handle_failure(db, delivery)

    await db.commit()
    await _release_delivery_lock(_redis, _lock_key, _lock_held, delivery_id)
    return delivery


async def _handle_failure(db: Any, delivery: WebhookDelivery, override_delay: float | None = None) -> None:
    """Handle failed delivery with retry logic.

    The retry schedule lives here — the single place that decides
    DEAD vs PENDING.
    """
    from src.app.modules.webhooks.service import WEBHOOK_RETRY_DELAYS

    attempt_idx = delivery.attempt_count - 1
    if attempt_idx >= 0 and attempt_idx < len(WEBHOOK_RETRY_DELAYS):
        # Schedule retry
        retry_delay = int(override_delay if override_delay is not None else WEBHOOK_RETRY_DELAYS[attempt_idx])
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay)
        delivery.status = WebhookDeliveryStatus.PENDING

        # Enqueue retry task. Delay is large enough that commit will finish first.
        # Avoid SQLAlchemy event.listen to prevent memory leak accumulation on rollback.
        delivery_id = delivery.id
        _spawn_enqueue(delivery_id, retry_delay, delivery.tenant_id)

        logger.warning(
            f"Webhook delivery failed, will retry in {retry_delay}s: {delivery.id} (attempt {delivery.attempt_count})"
        )
    else:
        # Max retries exhausted
        delivery.status = WebhookDeliveryStatus.FAILED
        logger.error(
            "Webhook delivery failed permanently after attempts:",
            attempt_count=delivery.attempt_count,
            id=delivery.id,
        )


# ---------------------------------------------------------------------------
# Lifecycle 3: recover (lost-enqueue harvester; merges recovery.py)
# ---------------------------------------------------------------------------


async def recover_stuck_deliveries(ctx: dict[str, Any]) -> int:
    """ARQ periodic job: find PENDING deliveries past their next_retry_at and re-enqueue.

    Merged from recovery.py: a stuck PENDING row with no running job is a
    LOST enqueue — the in-flight deliver loop can never see it because it
    only runs once a job exists. The W5 coalesce fallback covers rows whose
    next_retry_at is NULL (initial enqueue lost before the trigger set it).
    """
    recovered = 0
    buffer_minutes = settings.webhooks.WEBHOOK_RECOVERY_BUFFER_MINUTES
    cutoff_time = datetime.now(UTC) - timedelta(minutes=buffer_minutes)

    redis: ArqRedis | None = ctx.get("redis")
    if not redis:
        logger.error("Redis not available in context for webhook recovery")
        return 0

    async with get_db_context() as db:
        async with bypass_rls(db, reason="Webhook recovery job"):
            stmt = (
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == WebhookDeliveryStatus.PENDING,
                    func.coalesce(WebhookDelivery.next_retry_at, WebhookDelivery.created_at) < cutoff_time,
                )
                .limit(settings.webhooks.WEBHOOK_RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )

            result = await db.execute(stmt)
            deliveries = result.scalars().all()
            for delivery in deliveries:
                # NOTE: explicit _job_id (ARQ dedup) — a recovered row and a
                # retry job for the same delivery must not double-enqueue.
                await redis.enqueue_job(
                    "deliver_webhook_task",
                    delivery_id=str(delivery.id),
                    tenant_id=str(delivery.tenant_id),
                    _job_id=f"recover_webhook_{delivery.id}",
                )
                # Update next_retry_at to prevent immediate re-selection if job takes a while
                delivery.next_retry_at = datetime.now(UTC) + timedelta(minutes=10)
                recovered += 1

            if recovered > 0:
                await db.commit()

    if recovered > 0:
        logger.info("Recovered stuck webhook deliveries", count=recovered)

    return recovered


__all__ = [
    "deliver",
    "enqueue",
    "recover_stuck_deliveries",
]
