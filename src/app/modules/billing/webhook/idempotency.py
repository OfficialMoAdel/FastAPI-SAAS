"""Webhook idempotency — duplicate suppression under Stripe's at-least-once delivery.

Primary lock: Redis SET NX with a SHORT processing-window TTL (dedup window =
``_WEBHOOK_PROCESSING_TTL_SECONDS``, default 5 minutes).
Fallback: an atomic conditional claim on ``webhook_events`` when Redis is down.
Fail-closed: if both fail, the event is rejected so Stripe retries later
rather than risking duplicate side effects.

The DB fallback is not a degraded copy of the Redis path — it is the ONLY
mutual exclusion available during a Redis outage, so it has to be atomic in
its own right. Two deliveries of the same event must never both conclude
"mine to process". See ``_db_idempotency_check``.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from src.app.core.cache.client import cache_client
from src.app.core.redis_provider import get_redis
from src.app.modules.billing import webhook as _pkg
from src.app.shared.exceptions import ServiceUnavailableError


class EventLockResult(StrEnum):
    """Outcome of trying to acquire processing rights for an event.

    These are three genuinely different facts, and collapsing them is a bug:

    ``ACQUIRED``     this delivery owns the event; run the handler.
    ``IN_FLIGHT``    another delivery holds a LIVE claim. The caller must be
                     told to RETRY (HTTP 409) — the holder may still fail.
    ``ALREADY_DONE`` the event settled. Retrying is pointless; answer 200.
    """

    ACQUIRED = "acquired"
    IN_FLIGHT = "in_flight"
    ALREADY_DONE = "already_done"


async def _is_event_processed(event_id: str) -> bool:
    """B4: check DURABLE processed state (Redis terminal marker or DB row).

    Called before ``_try_acquire_event_lock`` so redeliveries arriving after
    the processing window are rejected by evidence that the event was
    fully handled — not just by a short-lived in-flight lock.

    Only a STAMPED row (``completed_at`` set) is evidence of completion. A
    claim row is evidence that someone started, not that anyone finished; the
    distinction is what stops a crash mid-handling from either dropping the
    event forever or replaying it forever.
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
            if not row:
                return False
            # Verify identity, not just existence: guards against test-double
            # sessions returning arbitrary truthy objects.
            if getattr(row, "event_id", None) != event_id:
                return False
            return getattr(row, "completed_at", None) is not None
    except Exception as e:
        _pkg.logger.error("Processed-marker DB check failed - failing closed", event_id=event_id, error=str(e))
        raise RuntimeError(f"Failed to verify webhook event idempotency for {event_id}: {e}") from e


async def _try_acquire_event_lock(event_id: str) -> tuple[EventLockResult, str]:
    """Acquire processing rights for ``event_id``.

    Returns ``(result, lock_token)``. ``lock_token`` is always a fresh unique
    value minted here — including on the DB-fallback path, where it is what
    records claim OWNERSHIP so a release can be scoped to this delivery.

    Why the result is three-valued: the caller used to receive a bare bool, so
    "another delivery is working on it" and "it is already finished" both
    arrived as ``False`` and both became the same HTTP status. That is wrong
    in opposite directions — answering 200 to an in-flight duplicate loses the
    event if the holder later fails, while answering 409 to a settled event
    makes Stripe retry a finished payment for days.

    Args:
        event_id: The Stripe event ID (e.g., "evt_1234567890")

    Returns:
        ``(ACQUIRED, token)``, ``(IN_FLIGHT, token)`` or ``(ALREADY_DONE, token)``.
    """
    token = uuid4().hex

    # NOTE: called through the package namespace (`_pkg`) on purpose. The
    # package docstring names this as a seam that tests patch; a direct local
    # call would silently bypass `mock.patch` and run the real thing.
    if cache_client.client is None:
        return await _pkg._db_idempotency_check(event_id, token), token

    key = f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
    try:
        # SET key value NX EX ttl — atomic set-if-not-exists holding OUR token.
        # This is a *processing* lock, NOT a durable marker; on success
        # `mark_event_processed` overwrites it with "processed" + a 7d TTL.
        acquired = await cache_client.set_nx(key, token, ttl=_pkg._WEBHOOK_PROCESSING_TTL_SECONDS)
        if acquired:
            return EventLockResult.ACQUIRED, token
        # The key exists. It is either a live in-flight lock or a terminal
        # marker left by an earlier success — `_is_event_processed` already
        # answered the terminal case before we got here, so this is in-flight.
        return EventLockResult.IN_FLIGHT, token
    except Exception as e:
        _pkg.logger.error(
            "Failed to acquire webhook event lock, falling back to DB check",
            event_id=event_id,
            error=str(e),
        )
        return await _pkg._db_idempotency_check(event_id, token), token


async def _db_idempotency_check(event_id: str, lock_token: str) -> EventLockResult:
    """Atomically claim ``event_id`` when Redis is unavailable.

    This is the only mutual exclusion that exists during a Redis outage, so it
    must be atomic. The mechanism is a single conditional UPDATE whose WHERE
    clause IS the election:

        * no row at all            -> INSERT and win outright
        * row stamped              -> somebody finished; ALREADY_DONE
        * row unstamped, claim     -> a LIVE handler owns it; IN_FLIGHT
          older than the TTL
        * row unstamped, claim     -> the conditional UPDATE steals it and
          younger than the TTL        rowcount 1 means THIS delivery won

    The earlier implementation instead caught ``IntegrityError`` and then
    SELECTed the losing row to ask "is it finished?". That is check-then-act:
    two deliveries that both lost the INSERT both read the same UNSTAMPED row
    — one written by the other delivery microseconds earlier — and both
    concluded "not finished, so it is mine". No age gate and no ownership
    marker, so this was not even a narrow race; it was the first ordinary
    redelivery. Two handlers ran concurrently and a paid invoice could be
    recorded twice.

    ``rowcount`` from the conditional UPDATE is what makes the election
    atomic: the database decides the winner, not the application.
    """
    from sqlalchemy import or_, update
    from sqlalchemy.exc import IntegrityError

    from src.app.modules.billing.models import WebhookEvent

    ttl = _pkg._WEBHOOK_ABANDONED_CLAIM_TTL_SECONDS
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=ttl)

    try:
        async with _pkg.job_bypass_context("Webhook DB idempotency check") as db:
            # Fast path: nobody has seen this event. The PK still elects a
            # single winner, so a duplicate insert is a lost race, not a bug.
            db.add(
                WebhookEvent(
                    event_id=event_id,
                    completed_at=None,
                    claimed_by=lock_token,
                    claimed_at=now,
                )
            )
            try:
                await db.commit()
                return EventLockResult.ACQUIRED
            except IntegrityError:
                await db.rollback()

            # Slow path: a row exists. Try to take it over IF it is provably
            # abandoned. This UPDATE is the election — exactly one caller can
            # observe rowcount == 1.
            result = await db.execute(
                update(WebhookEvent)
                .where(
                    WebhookEvent.event_id == event_id,
                    WebhookEvent.completed_at.is_(None),
                    or_(
                        WebhookEvent.claimed_at.is_(None),
                        WebhookEvent.claimed_at < stale_before,
                    ),
                )
                .values(claimed_by=lock_token, claimed_at=now)
            )
            await db.commit()
            if result.rowcount:
                _pkg.logger.warning(
                    "DB idempotency check: reclaimed an ABANDONED claim",
                    event_id=event_id,
                    abandoned_after_seconds=ttl,
                )
                return EventLockResult.ACQUIRED

            # We lost. Read the row only to LABEL the loss — the decision is
            # already made and cannot be talked out of by a concurrent change.
            from sqlalchemy import select

            existing = (
                await db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
            ).scalar_one_or_none()

            if existing is None:
                # The row vanished between our failed INSERT and this read —
                # a concurrent release deleted an un-stamped claim. Nobody owns
                # the event now, so the claim is free to take. The previous
                # code read `None` as "already processed" and answered 409 for
                # an event nobody was working on.
                db.add(
                    WebhookEvent(
                        event_id=event_id,
                        completed_at=None,
                        claimed_by=lock_token,
                        claimed_at=now,
                    )
                )
                await db.commit()
                return EventLockResult.ACQUIRED

            if getattr(existing, "completed_at", None) is not None:
                _pkg.logger.info(
                    "DB idempotency check: event already processed, skipping",
                    event_id=event_id,
                )
                return EventLockResult.ALREADY_DONE

            _pkg.logger.info(
                "DB idempotency check: claim held by a live delivery, asking Stripe to retry",
                event_id=event_id,
            )
            return EventLockResult.IN_FLIGHT
    except Exception as db_err:
        # If both Redis AND DB checks fail, fail closed.
        # Let Stripe retry later when the DB/Cache is healthy to avoid duplicates.
        _pkg.logger.error(
            "DB idempotency fallback also failed, rejecting processing",
            event_id=event_id,
            error=str(db_err),
        )
        raise ServiceUnavailableError("Service unavailable for idempotency check. Please retry.")


async def mark_event_processed(event_id: str, lock_token: str | None = None) -> None:
    """B4: record a DURABLE processed marker after successful handling.

    Strategy (belt and braces):
      1. Rewrite the Redis key as a long-TTL terminal marker (7 days).
      2. STAMP ``completed_at`` on the ``webhook_events`` row and release
         ``claimed_by`` — survives a Redis flush/outage, and
         ``_db_idempotency_check`` consults it when Redis is down.

    Failures here are logged but NOT raised: the side effects already
    happened, so rejecting the delivery would only cause pointless Stripe
    retries.
    """
    from sqlalchemy import select, update
    from sqlalchemy.exc import IntegrityError

    from src.app.modules.billing.models import WebhookEvent

    try:
        seven_days = 7 * 24 * 3600
        await cache_client.set(
            f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}",
            "processed",
            ttl=seven_days,
        )
    except Exception as e:
        _pkg.logger.error("Failed to write terminal webhook idempotency marker", event_id=event_id, error=str(e))

    try:
        async with _pkg.job_bypass_context("Webhook processed marker insert") as db:
            now = datetime.now(UTC)
            # Prefer stamping the existing claim in place. Scoping on
            # `claimed_by` when we hold a token means we can never stamp a row
            # that a concurrent delivery has already reclaimed for itself.
            stmt = update(WebhookEvent).where(
                WebhookEvent.event_id == event_id,
                WebhookEvent.completed_at.is_(None),
            )
            if lock_token is not None:
                stmt = stmt.where(WebhookEvent.claimed_by == lock_token)
            result = await db.execute(stmt.values(completed_at=now, claimed_by=None, claimed_at=None))

            if not result.rowcount:
                # Either no claim row exists (the Redis path carried
                # idempotency and never inserted one), or our token no longer
                # owns the claim. Distinguish before inserting, because
                # inserting blindly would resurrect a row a live delivery owns.
                existing = (
                    await db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
                ).scalar_one_or_none()
                if existing is None:
                    db.add(WebhookEvent(event_id=event_id, completed_at=now, claimed_by=None, claimed_at=None))
                    await db.commit()
                    return
                if getattr(existing, "completed_at", None) is not None:
                    # Already stamped by whoever really finished it. The Redis
                    # marker above is still correct; nothing more to do.
                    return
                _pkg.logger.warning(
                    "Completion stamp skipped: claim is owned by another delivery",
                    event_id=event_id,
                )
                return
            await db.commit()
    except IntegrityError:
        _pkg.logger.warning(
            "Concurrent claim row appeared while stamping completion; Redis marker stands",
            event_id=event_id,
        )
    except Exception as e:
        _pkg.logger.error("Failed to persist WebhookEvent processed row", event_id=event_id, error=str(e))


async def _release_webhook_event_lock(
    event_id: str,
    *,
    lock_token: str | None = None,
    escalate: bool = True,
) -> bool:
    """Release an in-flight claim so Stripe can retry.

    A failing delivery must undo ONLY ITS OWN claim. ``mark_event_processed``
    writes the terminal marker to the same Redis key and stamps the same row,
    so an unconditional release could erase the successful work of a delivery
    that had meanwhile finished:

        A starts, outlives the processing TTL
        B acquires, runs, succeeds -> writes "processed" + stamps the row
        A fails -> an unconditional release deleted B's marker and row
        C redelivers -> handler runs a second time -> double billing

    Two independent guards close that:

    * Redis — compare-and-delete against ``lock_token``.
    * DB     — delete only rows this delivery still owns
      (``claimed_by = lock_token`` AND ``completed_at IS NULL``). Scoping on
      ownership is what stops a Redis-path delivery from deleting an unstamped
      claim belonging to a concurrent DB-fallback delivery.

    Returns:
        True when the claim is released (or was already gone), False when a
        leg that had to run FAILED. The caller MUST NOT report success on
        False — that is exactly how a leaked claim outlived its cleanup job:
        the job swallowed every error, logged "cleaned up" and returned
        success, so nothing ever retried it.

    Args:
        event_id: The Stripe event id whose claim is being released.
        lock_token: The ownership token of the claim. Without it the Redis
            leg is skipped (there is nothing to compare against) and the DB
            leg runs WITHOUT the ownership predicate — so the caller must
            pass it whenever it knows it.
        escalate: Whether a failed release may enqueue the cleanup job. The
            cleanup job itself passes False: it IS the escalation, and
            letting it escalate again would schedule itself forever.
    """
    from sqlalchemy import delete

    from src.app.modules.billing.models import WebhookEvent

    # Track the legs we actually ATTEMPTED. The old code required both to have
    # succeeded before it would stay quiet, but on the DB-fallback path there
    # is no Redis claim to release by construction — so the alarm fired on
    # every single failure while Redis was down, including the ones where
    # nothing was actually leaked. Operators learn to ignore CRITICAL.
    redis_ok = True
    redis_attempted = False
    db_ok = False
    db_attempted = False

    if cache_client.client is not None:
        redis_attempted = True
        key = f"{_pkg._WEBHOOK_EVENT_KEY_PREFIX}{event_id}"
        if lock_token is None:
            _pkg.logger.info("webhook_lock_release_skipped_no_token", event_id=event_id)
        else:
            try:
                deleted = await _release_if_owned(key, lock_token)
                if not deleted:
                    # Someone else owns the key now — their marker stands.
                    _pkg.logger.info("webhook_lock_release_skipped_not_owner", event_id=event_id)
            except Exception as redis_e:
                redis_ok = False
                _pkg.logger.error("Failed to release webhook event lock:", redis_e=redis_e)

    db_attempted = True
    try:
        async with _pkg.job_bypass_context("Webhook DB unlock on error") as db:
            stmt = delete(WebhookEvent).where(
                WebhookEvent.event_id == event_id,
                WebhookEvent.completed_at.is_(None),
            )
            if lock_token is not None:
                stmt = stmt.where(WebhookEvent.claimed_by == lock_token)
            result = await db.execute(stmt)
            await db.commit()
            db_ok = True
            if not result.rowcount:
                _pkg.logger.info(
                    "webhook_db_release_skipped_not_owner",
                    event_id=event_id,
                    detail="claim was completed or is owned by another delivery",
                )
    except Exception as db_err:
        _pkg.logger.error("Failed to release DB webhook event lock:", db_err=str(db_err))

    # Escalate only on a leg we actually tried and that actually failed.
    if (redis_attempted and not redis_ok) or (db_attempted and not db_ok):
        _pkg.logger.critical(
            "Partial release of webhook locks for failed webhook. "
            "A stale claim may persist; Stripe retry could be falsely rejected.",
            event_id=event_id,
            redis_released=redis_ok,
            db_released=db_ok,
        )
        if escalate:
            try:
                from src.app.core.utils.queue import pool

                if pool:
                    # F10: the lock token travels with the cleanup job. The
                    # job used to be enqueued with the event id alone, so it
                    # could not compare-and-delete the Redis claim (the
                    # leg was skipped entirely) and its DB leg deleted
                    # WITHOUT the `claimed_by` predicate — it could remove
                    # ANOTHER delivery's claim. With the token, both legs
                    # are ownership-scoped.
                    await pool.enqueue_job(
                        "cleanup_leaked_webhook_lock",
                        event_id,
                        lock_token,
                        _defer_by=300,
                    )
            except Exception as q_err:
                _pkg.logger.error("Failed to enqueue fallback cleanup task for webhook lock:", error=str(q_err))
        return False
    return True


async def _release_if_owned(key: str, lock_token: str) -> bool:
    """Delete ``key`` only when it still holds ``lock_token`` (compare-and-delete).

    Returns True when the key was deleted by us, False when it was absent or
    now holds someone else's value (another delivery's terminal marker).
    """
    client = get_redis()
    if client is None:
        return False
    try:
        from src.app.core.utils.lua_manager import get_lua_manager

        result = await get_lua_manager().execute(
            client,
            "compare_and_delete",
            keys=[key],
            args=[lock_token],
        )
        return bool(int(result))
    except Exception as e:
        _pkg.logger.error("compare_and_delete failed for webhook lock", error=str(e))
        return False
