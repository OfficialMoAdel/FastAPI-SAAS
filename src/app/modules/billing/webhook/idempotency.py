"""Webhook idempotency — exactly-once processing under Stripe's at-least-once delivery.

Primary lock: Redis SET NX with a short processing-window TTL.
Fallback: DB insert-claim on the ``WebhookEvent`` table when Redis is down.
Fail-closed: if both fail, the event is rejected so Stripe retries later
rather than risking duplicate side effects.
"""

from fastapi import HTTPException

from src.app.modules.billing import webhook as _pkg


async def _try_acquire_event_lock(event_id: str) -> bool:
    """Atomically try to acquire processing lock for a webhook event.

    Primary: Redis SET with NX (set-if-not-exists) for atomic idempotency.
    Fallback: DB billing record check when Redis is unavailable.

    Args:
        event_id: The Stripe event ID (e.g., "evt_1234567890")

    Returns:
        True if lock acquired (event NOT previously processed), False if already processed
    """
    redis_client = _pkg.redis_client
    if redis_client is None:
        # Redis not available — fall back to DB idempotency check
        return await _pkg._db_idempotency_check(event_id)

    try:
        key = f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
        # SET key value NX EX ttl - atomic set-if-not-exists with a SHORT
        # processing-window TTL. This is a *processing* lock, NOT the
        # "processed" marker. The durable marker is written only AFTER the
        # handler commits (see _mark_event_processed), so a crash mid-handling
        # lets the short lock expire and Stripe retry instead of silently
        # losing the event for 7 days.
        result = await redis_client.set(key, "processing", nx=True, ex=_pkg._WEBHOOK_PROCESSING_TTL_SECONDS)
        return result is not None  # True = lock acquired, False = already processed
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
        raise HTTPException(status_code=503, detail="Service unavailable for idempotency check. Please retry.")


async def _release_webhook_event_lock(event_id: str) -> None:
    """Release Redis and DB locks if event processing fails."""
    lock_released = False

    # Do NOT mark as processed on failure - allow Stripe to retry
    # CRITICAL FIX: We MUST delete the Redis lock we just acquired, otherwise
    # Stripe's retries will be false-positively rejected as "already_processed" for 7 days!
    redis_client = _pkg.redis_client
    if redis_client:
        try:
            await redis_client.delete(f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}")
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
