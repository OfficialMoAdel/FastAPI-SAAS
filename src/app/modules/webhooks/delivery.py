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

import math
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.config import settings
from src.app.core.db.database import get_db_context
from src.app.core.db.rls import bypass_rls
from src.app.core.logger import get_logger
from src.app.core.security.encryption import decrypt_data
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules._base import ModelNotFoundError
from src.app.modules.audit_logs import AuditGuarantee, audit
from src.app.modules.webhooks.http_client import get_webhook_http_client
from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint
from src.app.modules.webhooks.signer import canonical_payload_bytes, sign_canonical_bytes

if TYPE_CHECKING:
    from arq.connections import ArqRedis

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Retry schedule — canonical home (single place that decides DEAD vs PENDING).
# Values come from settings.webhooks.WEBHOOK_RETRY_DELAYS so operators can
# override them; the fallback is 1m, 5m, 15m, 30m, 1h (5 retries over 1 hour).
# service.py re-exports these names for backward compatibility.
# ---------------------------------------------------------------------------

RETRY_DELAY_1_MINUTE = 60
RETRY_DELAY_5_MINUTES = 300
RETRY_DELAY_15_MINUTES = 900
RETRY_DELAY_30_MINUTES = 1800
RETRY_DELAY_1_HOUR = 3600

WEBHOOK_RETRY_DELAYS: list[int] = list(
    getattr(settings.webhooks, "WEBHOOK_RETRY_DELAYS", None)
    or [RETRY_DELAY_1_MINUTE, RETRY_DELAY_5_MINUTES, RETRY_DELAY_15_MINUTES, RETRY_DELAY_30_MINUTES, RETRY_DELAY_1_HOUR]
)
"""Import-time snapshot of the retry schedule (kept for backward compat —
service.py re-exports this name and tests pin it)."""


def get_retry_delays() -> list[int]:
    """Read the retry schedule live from settings (F24).

    The module constant above is frozen at import, so a post-import settings
    override (tests, operator reload) never took effect. Scheduling decisions
    go through this reader; the constant remains as the documented default.
    """
    live = getattr(settings.webhooks, "WEBHOOK_RETRY_DELAYS", None)
    if live:
        return list(live)
    return list(WEBHOOK_RETRY_DELAYS)


# ---------------------------------------------------------------------------
# Lock helper (R1-17)
# ---------------------------------------------------------------------------


async def _release_delivery_lock(lock_key: str, lock_token: str, delivery_id: UUID) -> None:
    """Release the webhook delivery lock, logging failures (R1-17).

    D8: the release is OWNERSHIP-CHECKED. A blind delete lets a slow
    delivery whose lock already expired delete the lock another delivery
    acquired in the meantime — and the delivery lock exists precisely to
    stop two deliveries of the same row from racing. The lock carries a
    60s TTL, so the worst case is a delayed redelivery, but operators
    should see the error.
    Goes through the CacheClient seam (None-safe: noop when Redis is down).
    """
    try:
        from src.app.core.cache.client import cache_client

        await cache_client.release_lock_if_owned(lock_key, lock_token)
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
    """Fire-and-forget enqueue from a running loop.

    ``create_safe_task`` owns the failure handling; the recovery cron is the
    safety net for a lost enqueue. This is only called from ``deliver()``,
    which is async, so a second no-loop fallback would be unreachable.
    """
    from src.app.core.utils.background_task import create_safe_task

    create_safe_task(enqueue(delivery_id, defer_by=defer_by, tenant_id=tenant_id))


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
    # The claim goes through the CacheClient seam (None-safe: when Redis is
    # down the claim reports not-held and delivery proceeds unserialized).
    from src.app.core.cache.client import cache_client
    from src.app.core.resilience.registry import get_webhook_breaker
    from src.app.core.utils.circuit_breaker import CircuitBreakerOpen, CircuitState

    _lock_key = f"webhook:deliver_lock:{delivery_id}"
    # D8: the lock carries an OWNERSHIP TOKEN, and the release is
    # ownership-checked — a delivery that overran its TTL cannot delete the
    # lock a later delivery acquired in the meantime.
    _lock_token = uuid4().hex
    _lock_held = await cache_client.set_nx(_lock_key, _lock_token, ttl=60)
    if cache_client.client is not None and not _lock_held:
            logger.info("Webhook delivery already in progress, skipping", delivery_id=delivery_id)
            existing = await db.get(WebhookDelivery, delivery_id)
            if not existing:
                raise ModelNotFoundError("WebhookDelivery", delivery_id)
            return existing

    # D6: the HALF_OPEN probe slot and the breaker that holds it are declared
    # before the try so the finally below can release the slot on EVERY exit
    # path — including the ones that never reach the gate.
    breaker: Any = None
    probe_claimed = False

    try:
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
            raise ModelNotFoundError("WebhookDelivery", delivery_id)

        # E8: a delivery that already reached a terminal state must not be
        # POSTed again. The delivery lock serialises CONCURRENT attempts; it
        # does not stop a LATER one (the recovery sweep and the retry ladder
        # both re-enqueue). Without this check a DELIVERED row could be sent
        # a second time and the receiver would see the same payload twice
        # under the same X-Webhook-ID.
        if delivery.status in (WebhookDeliveryStatus.DELIVERED, WebhookDeliveryStatus.FAILED):
            logger.info(
                "webhook_delivery_already_terminal",
                delivery_id=str(delivery_id),
                status=delivery.status.value,
            )
            return delivery

        # Access pre-resolved or eagerly loaded endpoint
        if endpoint is None:
            endpoint = delivery.webhook_endpoint
        if not endpoint:
            raise ModelNotFoundError("WebhookEndpoint", delivery.webhook_endpoint_id)
        # Check circuit breaker before attempting delivery
        # P2-B: scoped per endpoint — one dead receiver must not trip deliveries
        # for every other tenant/endpoint sharing the process.
        breaker = get_webhook_breaker(str(endpoint.id))
        # D7: `is_closed` is True during the virtual HALF_OPEN, so every
        # concurrent delivery passed the gate and a recovering endpoint took
        # the whole burst. The breaker now admits every delivery while
        # CLOSED, none while OPEN, and exactly ONE while HALF_OPEN (the
        # probe); the deliveries that are not the probe are deferred to the
        # retry ladder, exactly as an OPEN circuit defers them.
        # Fast path: CLOSED admits without the lock and owns no probe. Every
        # other state goes through the gate, which returns the claim flag.
        if breaker.state == CircuitState.CLOSED:
            allowed, probe_claimed = True, False
        else:
            allowed, probe_claimed = await breaker.allow_single_flight_with_claim()
        if not allowed:
            import time

            current_time = time.time()
            # Clamp at 0: a breaker opened without a recorded failure time
            # (or clock adjustments) otherwise yields a hugely negative window,
            # scheduling next_retry_at in the past.
            recovery_time = max(0.0, breaker.recovery_timeout - (current_time - breaker.last_failure_time))
            logger.warning(
                "webhook_circuit_open_delivery_skipped",
                delivery_id=delivery_id,
                recovery_in_s=recovery_time,
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
            # F25: _spawn_enqueue catches RuntimeError internally and never
            # re-raises — the outer handler was dead code. Call directly; a
            # lost enqueue is the recovery cron's job (safety net by design).
            _spawn_enqueue(delivery_id, recovery_delay, delivery.tenant_id)
            return delivery

        # D6: the probe slot is released on EVERY exit path below (success,
        # failure, the SSRF early return, a cancellation). A claim that is
        # never released blocks this endpoint's next recovery window.
        # `probe_claimed` comes from the gate itself: a call admitted while
        # CLOSED does not own a probe and must not release one on exit.

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
        # P1-5 FIX: prefer signing_secret (what rotate_signing_secret rotates)
        # over the legacy secret — mirroring the trigger path's preference in
        # service.py. Previously the wire signature was always computed from
        # endpoint.secret, so rotation never took effect on delivered headers and
        # a compromised secret could not be revoked.
        if endpoint.signing_secret:
            active_secret = decrypt_data(endpoint.signing_secret)
        else:
            active_secret = decrypt_data(endpoint.secret)
        signature_header = sign_canonical_bytes(payload_bytes, active_secret, send_timestamp)
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
                    old_digest = sign_canonical_bytes(
                        payload_bytes, decrypt_data(endpoint.old_signing_secret), send_timestamp
                    )
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
                guarantee=AuditGuarantee.BEST_EFFORT,
            )

            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.response_body = f"SSRF protection: {e}"
            await db.commit()
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

        # S-41 FIX: accumulator for the post-commit retry enqueue.
        pending_retry: tuple[UUID, int, UUID | None] | None = None

        # Process HTTP result
        if response is not None:
            delivery.attempt_count += 1
            delivery.response_status = response.status_code
            delivery.response_status_code = response.status_code
            # F26: response.text re-decodes per charset and raises on
            # undecodable binary (job 500s instead of recording failure).
            # Decode raw bytes with replacement so any payload is recordable.
            delivery.response_body = bytes(response.content[:10000]).decode("utf-8", errors="replace")

            if 200 <= response.status_code < 300:  # HTTP success range
                # Success - record with circuit breaker
                await breaker.record_success()
                delivery.status = WebhookDeliveryStatus.DELIVERED
                delivery.delivered_at = datetime.now(UTC)
                delivery.error_message = None
                logger.info(
                    "webhook_delivered",
                    delivery_id=str(delivery_id),
                    attempt_count=delivery.attempt_count,
                )
            else:
                # HTTP error - record failure and retry if attempts remain
                await breaker.record_failure(Exception(f"HTTP {response.status_code}"))
                delivery.error_message = f"HTTP {response.status_code}"

                # SECURITY FIX: Do not retry non-transient 4xx errors (e.g. 400, 422)
                if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                    delivery.status = WebhookDeliveryStatus.FAILED
                    logger.error(
                        "Webhook failed with permanent 4xx error, skipping retries",
                        delivery_id=delivery_id,
                        status_code=response.status_code,
                    )
                else:
                    pending_retry = await _handle_failure(db, delivery)

        elif circuit_error is not None:
            # Circuit opened during delivery attempt
            logger.warning("webhook_circuit_opened", error=str(circuit_error))
            delivery.attempt_count += 1
            delivery.response_body = str(circuit_error)[:1000]
            delivery.error_message = str(circuit_error)[:1000]
            pending_retry = await _handle_failure(
                db, delivery, override_delay=getattr(circuit_error, "recovery_in", None)
            )

        elif http_error is not None:
            # Network/timeout error - record failure and retry
            logger.error("Webhook delivery failed", delivery_id=delivery_id, http_error=http_error)
            await breaker.record_failure(http_error)
            delivery.attempt_count += 1
            delivery.response_body = str(http_error)[:1000]
            delivery.error_message = str(http_error)[:1000]
            pending_retry = await _handle_failure(db, delivery)

        await db.commit()

        # S-41 FIX: enqueue only now that the attempt_count / next_retry_at /
        # status writes are DURABLE. Spawning earlier raced the commit and could
        # re-POST the same payload to the customer (see _handle_failure).
        if pending_retry is not None:
            _spawn_enqueue(*pending_retry)

        return delivery
    finally:
        # D6: release the HALF_OPEN probe slot on every exit path. The
        # claim is what makes exactly one delivery probe a recovering
        # endpoint; a claim that is never released blocks this endpoint's
        # next recovery window (the delivery ladder would defer every
        # delivery for a whole window).
        if probe_claimed and breaker is not None:
            breaker.release_probe()
        # F6: every exit path releases the claim (covers the raises the
        # inline releases missed: missing endpoint, re-fetch miss,
        # decrypt/commit/breaker failures). Double-release is a harmless
        # second DEL of a 60s-TTL key.
        await _release_delivery_lock(_lock_key, _lock_token, delivery_id)


async def _handle_failure(
    db: Any,
    delivery: WebhookDelivery,
    override_delay: float | None = None,
) -> tuple[UUID, int, UUID | None] | None:
    """Handle failed delivery with retry logic.

    The retry schedule lives here — the single place that decides
    DEAD vs PENDING.

    S-41 FIX: returns the ``(delivery_id, defer_seconds, tenant_id)`` to
    enqueue, or None when the delivery is dead. The caller MUST spawn the
    enqueue only AFTER its ``await db.commit()``.

    The enqueue used to fire from inside this function, which runs BEFORE the
    commit in ``deliver()``. The code relied on "delay is large enough that
    commit will finish first" — true for the 60s+ schedule, but NOT for the
    circuit-open path, where ``override_delay`` collapses the defer to
    ``max(1, ceil(recovery_in))`` ≈ 1 second. If the commit lost that race, the
    ARQ retry re-read the row from the DB, saw the PRE-attempt
    ``attempt_count`` / ``next_retry_at``, and re-POSTed the same payload to
    the customer — a duplicate webhook delivery caused by our own bookkeeping
    race, not by the receiver.

    Deferring to post-commit removes the race entirely.
    """
    attempt_idx = delivery.attempt_count - 1
    delays = get_retry_delays()
    if attempt_idx >= 0 and attempt_idx < len(delays):
        # Schedule retry
        # F33: ceil (never truncate) a sub-second circuit override to at
        # least 1s — int(0.5) scheduled an immediate retry hot-loop.
        raw_delay = override_delay if override_delay is not None else delays[attempt_idx]
        # Jitter: the ladder is EXACT ([60, 300, 900, 1800, 3600]), so when a
        # shared upstream fails, every endpoint's deliveries are deferred to
        # the same second and all re-enqueue together — a thundering herd on
        # recovery, aimed at both the worker pool and the endpoint that just
        # came back up. The periodic sweep compounds it by re-enqueueing the
        # whole PENDING set in one pass.
        #
        # +/-10% is enough to decorrelate without disturbing the operator-
        # visible schedule. `with_retry(jitter=True)` exists for exactly this
        # reason, but the webhook ladder bypasses with_retry and uses its own
        # schedule, so it never inherited the jitter.
        retry_delay = max(1, int(math.ceil(raw_delay * random.uniform(0.9, 1.1))))
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay)
        delivery.status = WebhookDeliveryStatus.PENDING

        logger.warning(
            "webhook_retry_scheduled",
            delivery_id=str(delivery.id),
            retry_delay=retry_delay,
            attempt_count=delivery.attempt_count,
        )
        return (delivery.id, retry_delay, delivery.tenant_id)

    # Max retries exhausted
    delivery.status = WebhookDeliveryStatus.FAILED
    logger.error(
        "webhook_delivery_failed_permanently",
        delivery_id=str(delivery.id),
        attempt_count=delivery.attempt_count,
    )
    return None


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
                # F8: claim the delivery lock before re-enqueueing. A running
                # deliver() holds this same lock, so a failed claim means the
                # row is in-flight (not stuck) — skip it instead of
                # double-delivering. The old _job_id-only dedup did not cover
                # recovery-vs-inflight (different job ids).
                # F7: per-row isolation — one bad row (or a lost Redis) must
                # not discard the whole batch's progress; commit per row.
                row_lock_key = f"webhook:deliver_lock:{delivery.id}"
                # D8: the recovery claim carries an OWNERSHIP TOKEN and is
                # released with an ownership check, so a recovery that
                # overran its TTL cannot delete the lock the delivery
                # itself acquired in the meantime.
                recovery_token = uuid4().hex
                try:
                    from src.app.core.cache.client import cache_client as _cc

                    claimed = await _cc.set_nx(row_lock_key, recovery_token, ttl=60)
                    if _cc.client is not None and not claimed:
                        continue
                    # E6: release the claim BEFORE the enqueue.
                    #
                    # The claim on the DELIVERY lock is the atomic "is a
                    # delivery in flight?" check — that is why it takes that
                    # key. But holding it while the job was enqueued made
                    # the job find the lock held, log "already in progress"
                    # and skip the delivery: the recovered row was never
                    # sent, and was only re-harvested ~10 minutes later.
                    # Releasing first lets the enqueued job take the lock
                    # itself; the ARQ job id (`recover_webhook_{id}`) keeps a
                    # second tick from enqueueing the same row twice.
                    if _cc.client is not None:
                        await _cc.release_lock_if_owned(row_lock_key, recovery_token)
                    # NOTE: explicit _job_id (ARQ dedup) — two recovery ticks
                    # must not double-enqueue the same row.
                    await redis.enqueue_job(
                        "deliver_webhook_task",
                        delivery_id=str(delivery.id),
                        tenant_id=str(delivery.tenant_id),
                        _job_id=f"recover_webhook_{delivery.id}",
                    )
                    # Update next_retry_at to prevent immediate re-selection if job takes a while
                    delivery.next_retry_at = datetime.now(UTC) + timedelta(minutes=10)
                    await db.commit()
                    recovered += 1
                except Exception as row_exc:  # noqa: BLE001
                    logger.error(
                        "webhook_recovery_row_failed",
                        delivery_id=str(delivery.id),
                        error=str(row_exc),
                    )
                    await db.rollback()
                    continue
                finally:
                    try:
                        from src.app.core.cache.client import cache_client as _cc2

                        # Release the claim so the enqueued job's own deliver()
                        # can acquire the lock at start; TTL bounds a crash leak.
                        # Best-effort: a failed release only delays one harvest.
                        if _cc2.client is not None:
                            await _cc2.release_lock_if_owned(row_lock_key, recovery_token)
                    except Exception:  # noqa: BLE001
                        pass

    if recovered > 0:
        logger.info("Recovered stuck webhook deliveries", count=recovered)

    return recovered


__all__ = [
    "deliver",
    "enqueue",
    "recover_stuck_deliveries",
    "WEBHOOK_RETRY_DELAYS",
    "RETRY_DELAY_1_MINUTE",
    "RETRY_DELAY_5_MINUTES",
    "RETRY_DELAY_15_MINUTES",
    "RETRY_DELAY_30_MINUTES",
    "RETRY_DELAY_1_HOUR",
]
