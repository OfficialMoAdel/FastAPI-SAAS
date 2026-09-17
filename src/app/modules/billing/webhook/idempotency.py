"""Webhook idempotency — duplicate suppression under Stripe's at-least-once delivery.

Primary lock: Redis SET NX with a SHORT processing-window TTL (dedup window =
``_WEBHOOK_PROCESSING_TTL_SECONDS``, default 5 minutes). Handlers MUST be
internally idempotent (upserts, exists-checks) because a Stripe redelivery more
than one dedup window after the first delivery will be reprocessed.
Fallback: DB insert-claim on the ``WebhookEvent`` table when Redis is down.
Fail-closed: if both fail, the event is rejected so Stripe retries later
rather than risking duplicate side effects.
"""

from src.app.core.cache.client import cache_client
from src.app.modules.billing import webhook as _pkg
from src.app.shared.exceptions import ServiceUnavailableError


async def _is_event_processed(event_id: str) -> bool:
    """B4: check DURABLE processed state (Redis terminal marker or DB row).

    Called before ``_try_acquire_event_lock`` so redeliveries arriving after
    the 300s processing window are rejected by evidence that the event was
    fully handled — not just by a short-lived in-flight lock.
    """
    try:
        value = await cache_client.get(f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}")
        if value == "processed":
            return True
    except Exception as e:
        _pkg.logger.warning(
            "Processed-marker Redis check failed, falling back to DB", event_id=event_id, error=str(e)
        )

    # Redis down / marker missing: consult the durable WebhookEvent row.
    from sqlalchemy import select

    from src.app.modules.billing.models import WebhookEvent

    try:
        async with _pkg.job_bypass_context("Webhook processed marker lookup") as db:
            result = await db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
            row = result.scalar_one_or_none()
            # Verify identity, not just existence: guards against test-double
            # sessions returning arbitrary truthy objects.
            return bool(row) and getattr(row, "event_id", None) == event_id
    except Exception as e:
        _pkg.logger.error("Processed-marker DB check failed - failing closed", event_id=event_id, error=str(e))
        raise RuntimeError(f"Failed to verify webhook event idempotency for {event_id}: {e}") from e


async def _try_acquire_event_lock(event_id: str) -> bool:
    """Atomically try to acquire processing lock for a webhook event.

    Primary: Redis SET with NX (set-if-not-exists) for atomic idempotency.
    Fallback: DB billing record check when Redis is unavailable.

    Args:
        event_id: The Stripe event ID (e.g., "evt_1234567890")

    Returns:
        True if lock acquired (event NOT previously processed), False if already processed
    """
    if cache_client.client is None:
        # Redis not available — fall back to DB idempotency check
        return await _pkg._db_idempotency_check(event_id)

    try:
        key = f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
        # SET key value NX EX ttl - atomic set-if-not-exists with a SHORT
        # processing-window TTL. This is a *processing* lock, NOT a durable
        # "processed" marker: no durable marker is written on success (handlers
        # are expected to be internally idempotent), so a crash mid-handling or
        # a late Stripe redelivery outside the TTL window simply reprocesses.
        acquired = await cache_client.set_nx(key, "processing", ttl=_pkg._WEBHOOK_PROCESSING_TTL_SECONDS)
        return acquired  # True = lock acquired, False = already processed
    except Exception as e:
        _pkg.logger.error(
            "Failed to acquire webhook event lock, falling back to DB check",
            event_id=event_id,
            error=str(e),
        )
        # Fall back to DB idempotency check instead of failing open
        return await _pkg._db_idempotency_check(event_id)


async def _db_idempotency_check(event_id: str) -> bool:
    """DB-based idempotency fallback for when Redis is unavailable.

    Atomically claims the event by inserting a ``WebhookEvent`` row; the
    unique ``event_id`` PK makes a duplicate insert fail with ``IntegrityError``,
    which we treat as "already processed". On failure we delete the row
    (see ``_release_webhook_event_lock``) so Stripe can safely retry.

    Args:
        event_id: The Stripe event ID

    Returns:
        True if event should be processed (not yet seen), False if duplicate
    """
    from sqlalchemy.exc import IntegrityError

    from src.app.modules.billing.models import WebhookEvent

    try:
        async with _pkg.job_bypass_context("Webhook DB idempotency check") as db:
            event = WebhookEvent(event_id=event_id)
            db.add(event)
            try:
                await db.commit()
                return True  # Successfully inserted, safe to process
            except IntegrityError:
                await db.rollback()
                _pkg.logger.info(
                    "DB idempotency check: event already processed, skipping",
                    event_id=event_id,
                )
                return False  # Already processed
    except Exception as db_err:
        # If both Redis AND DB checks fail, fail closed.
        # Let Stripe retry later when the DB/Cache is healthy to avoid duplicates.
        _pkg.logger.error(
            "DB idempotency fallback also failed, rejecting processing",
            event_id=event_id,
            error=str(db_err),
        )
        raise ServiceUnavailableError("Service unavailable for idempotency check. Please retry.")


async def mark_event_processed(event_id: str) -> None:
    """B4: record a DURABLE processed marker after successful handling.

    The SET NX lock acquired by ``_try_acquire_event_lock`` is only a short
    processing-window lock (default 300s). Without a durable marker, a Stripe
    redelivery arriving after that window fully re-executes the handler.

    Strategy (belt and braces):
      1. Rewrite the Redis key as a long-TTL terminal marker (7 days).
      2. Insert a ``WebhookEvent`` row (unique PK on event_id) — survives a
         Redis flush/outage, and ``_db_idempotency_check`` consults it when
         Redis is down.
    Failures here are logged but NOT raised: the side effects already
    happened, so rejecting the delivery would only cause pointless Stripe
    retries; handlers are internally idempotent as a last resort.
    """
    try:
        seven_days = 7 * 24 * 3600
        await cache_client.set(
            f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}",
            "processed",
            ttl=seven_days,
        )
    except Exception as e:
        _pkg.logger.error("Failed to write terminal webhook idempotency marker", event_id=event_id, error=str(e))

    from sqlalchemy.exc import IntegrityError

    from src.app.modules.billing.models import WebhookEvent

    try:
        async with _pkg.job_bypass_context("Webhook processed marker insert") as db:
            db.add(WebhookEvent(event_id=event_id))
            await db.commit()
    except IntegrityError:
        # Already claimed during a Redis-down fallback earlier in this flow.
        pass
    except Exception as e:
        _pkg.logger.error("Failed to persist WebhookEvent processed row", event_id=event_id, error=str(e))



async def _release_webhook_event_lock(event_id: str) -> None:
    # Do NOT mark as processed on failure - allow Stripe to retry
    # CRITICAL FIX: We MUST delete the Redis lock we just acquired, otherwise
    # Stripe's retries will be false-positively rejected as "already_processed" for 7 days!
    lock_released = False
    if cache_client.client is not None:
        try:
            await cache_client.delete(f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}")
            lock_released = True
        except Exception as redis_e:
            _pkg.logger.error("Failed to release webhook event lock:", redis_e=redis_e)

    # Also remove from DB to allow retries if DB fallback was used
    from sqlalchemy import delete

    from src.app.modules.billing.models import WebhookEvent

    try:
        async with _pkg.job_bypass_context("Webhook DB unlock on error") as db:
            await db.execute(delete(WebhookEvent).where(WebhookEvent.event_id == event_id))
            await db.commit()
            lock_released = True
    except Exception as db_err:
        _pkg.logger.error("Failed to release DB webhook event lock:", db_err=str(db_err))

    if not lock_released:
        _pkg.logger.critical(
            "Failed to release BOTH Redis and DB locks for failed webhook. "
            "Stripe retries will be falsely rejected! Manual intervention required.",
            event_id=event_id,
        )
        # Enqueue a fallback cleanup task
        try:
            from src.app.core.utils.queue import pool

            if pool:
                await pool.enqueue_job("cleanup_leaked_webhook_lock", event_id, _defer_by=300)
        except Exception as q_err:
            _pkg.logger.error("Failed to enqueue fallback cleanup task for webhook lock:", error=str(q_err))
