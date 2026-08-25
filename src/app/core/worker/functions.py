import asyncio
from typing import Any
from uuid import UUID

from arq.worker import Worker
from sqlalchemy import text

# uvloop is deliberately not initialized here at import time to prevent globally
# overriding the web server's event loop policy silently.
# Centralized logging configured via src/app/core/logger.py (structlog)
from src.app.core.logger import get_logger
from src.app.core.utils.job_wrapper import monitor_job
from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.modules.webhooks.service import WebhookService

logger = get_logger(__name__)


# -------- background tasks --------
@monitor_job("sample_background_task")
async def sample_background_task(ctx: Worker, name: str, tenant_id: UUID | str | None = None) -> str:
    await asyncio.sleep(5)
    return f"Task {name} is complete!"


@monitor_job("deliver_webhook_task")
async def deliver_webhook_task(ctx: Worker, delivery_id: UUID | str, tenant_id: UUID | str | None = None) -> str:
    """Background task to deliver a webhook.

    Note: delivery_id may be str after orjson deserialization (UUID → str → str).
    When tenant_id is passed as a job arg, skips the extra DB session lookup.
    """
    # Convert string to UUID if needed (orjson deserializes UUID as string)
    if isinstance(delivery_id, str):
        # BUG FIX: Wrap UUID conversion to provide a clear error on malformed input.
        # Previously, a bare ValueError from UUID() would surface with no context.
        try:
            delivery_id = UUID(delivery_id)
        except ValueError as e:
            raise ValueError(
                f"Invalid UUID format for delivery_id: {delivery_id!r}. "
                "Expected a valid UUID string (e.g. '550e8400-e29b-41d4-a716-446655440000')."
            ) from e

    # Use tenant_id from job arg if available (saves 1 DB session),
    # otherwise fall back to DB lookup for backward compatibility
    if tenant_id is not None:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    else:
        # Fallback: lookup tenant_id from delivery record (legacy path)
        async with job_bypass_context("Fetching tenant_id for webhook delivery") as db:
            db_result = await db.execute(
                text("SELECT tenant_id FROM webhook_deliveries WHERE id = :id"),
                {"id": str(delivery_id)},
            )
            row = db_result.fetchone()
            if not row:
                raise ValueError(f"Webhook delivery {delivery_id} not found")
            tenant_id = row[0]

    # Now process with proper tenant context
    async with job_tenant_context(tenant_id) as db:
        service = WebhookService(db)
        try:
            await service.deliver_webhook(delivery_id)
            return f"Webhook {delivery_id} delivered."
        except Exception as e:
            logger.error(f"Failed to deliver webhook {delivery_id}: {e}")
            raise


@monitor_job("migrate_sessions_task")
async def migrate_sessions_task(ctx: Worker) -> str:
    """Background task to migrate sessions to hash format."""
    from src.app.core.security.session_manager import session_manager

    logger.info("Starting session migration task")
    try:
        count = await session_manager.migrate_to_hash()
        return f"Migrated {count} users to hash format."
    except Exception as e:
        logger.error(f"Session migration failed: {e}")
        raise


@monitor_job("flush_audit_logs_task")
async def flush_audit_logs_task(ctx: dict) -> str:
    """Periodic task to flush buffered audit logs to database.

    Should be scheduled as a cron job (every 10-60 seconds).
    Drains the Redis buffer completely in batches of 100, up to MAX_FLUSH_PER_RUN per run.

    System task - requires RLS bypass.
    """
    from src.app.modules.audit_logs.service import (
        AUDIT_LOG_MAX_FLUSH_PER_RUN,
        flush_audit_log_buffer,
    )

    logger.info("Starting audit log flush task")
    total = 0

    async with job_bypass_context("Flushing audit logs to database") as db:
        try:
            # Drain buffer with limit per run to prevent long-running tasks
            while total < AUDIT_LOG_MAX_FLUSH_PER_RUN:
                remaining = AUDIT_LOG_MAX_FLUSH_PER_RUN - total
                # Pass the session to ensure RLS bypass works correctly
                count = await flush_audit_log_buffer(max_flush_per_run=remaining, db=db)
                if count == 0:
                    break
                total += count
        except Exception as e:
            logger.error(f"Failed to flush audit logs: {e}")
            raise

    return f"Flushed {total} audit logs."


# -------- base functions --------
@monitor_job("cleanup_expired_tokens_task")
async def cleanup_expired_tokens_task(ctx: dict) -> str:
    """Periodic task to remove expired tokens from Redis blacklist ZSET.

    Prevents unbounded memory growth of the token:blacklist sorted set.
    """
    from src.app.core.security.token_blacklist_cache import remove_expired_tokens

    try:
        removed = await remove_expired_tokens()
        if removed > 0:
            logger.info("Cleaned up expired tokens from blacklist", count=removed)
        return f"Removed {removed} expired tokens."
    except Exception as e:
        logger.error("Failed to cleanup expired tokens", error=str(e))
        raise


@monitor_job("cleanup_leaked_webhook_lock")
async def cleanup_leaked_webhook_lock(ctx: Worker, event_id: str) -> str:
    """B8: release a webhook idempotency lock whose release path failed.

    ``_release_webhook_event_lock`` enqueues this when BOTH the Redis delete
    and the DB unlock fail. Previously this function was enqueued but never
    defined or registered, so the orphaned WebhookEvent row permanently lost
    the event (every retry rejected as already_processed).

    Retries the same dual release; raises so arq/monitoring record the failure.
    """
    from src.app.modules.billing.webhook.idempotency import _release_webhook_event_lock

    await _release_webhook_event_lock(event_id)
    logger.info("Leaked webhook lock cleaned up", event_id=event_id)
    return f"Released lock for {event_id}"


async def startup(ctx: Worker) -> None:
    """Initialize the worker-process runtime.

    K2 FIX: the web lifespan performs this wiring (main.py), but the worker
    process runs its own interpreter and previously started with:
      - cache.client = None  -> flush_audit_logs_task returned 0 forever,
        cleanup_expired_tokens_task was a no-op
      - event_bus with no handlers / no ARQ queue -> publishes inside jobs
        (e.g. RLS-bypass audit events) were silently dropped
      - session_manager without a Redis client
    """
    from src.app.core.config import settings
    from src.app.core.redis_provider import get_redis_pool, set_clients

    if settings.REDIS_CACHE_ENABLED or settings.REDIS_QUEUE_ENABLED:
        pool = await get_redis_pool()
        # decode_responses=True matches the web-side cache client contract.
        import redis.asyncio as aioredis

        redis_client = aioredis.Redis(connection_pool=pool, decode_responses=True)
        await set_clients(redis_client=redis_client, redis_pool=pool)

        # Event bus: handlers + Redis client (DLQ persistence) + ARQ queue so
        # NORMAL/LOW events published inside jobs are queued, not dropped.
        from src.app.core.events import event_bus

        event_bus.set_redis_client(redis_client)
        if settings.ARQ_ENABLED:
            from arq.connections import ArqRedis

            from src.app.core.json_utils import json_dumps, json_loads

            def _serialize(obj: dict) -> bytes:
                return json_dumps(obj)  # type: ignore[return-value]  # orjson returns bytes when as_str=False

            def _deserialize(data: bytes | str | memoryview) -> Any:
                return json_loads(data)

            arq_pool = ArqRedis(
                connection_pool=pool,
                job_serializer=_serialize,
                job_deserializer=_deserialize,
            )
            event_bus.set_arq_queue(arq_pool)

        event_bus.register_all_handlers()

        # Session manager needs the shared client for Lua-based registry ops.
        from src.app.core.security.session_manager import session_manager

        session_manager.set_redis_client(redis_client)

    logger.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    """Tear down the worker runtime in reverse order."""
    from src.app.core.events import reset_event_bus
    from src.app.core.redis_provider import shutdown as shutdown_clients

    try:
        reset_event_bus()
    except Exception as e:  # noqa: BLE001 - teardown must not mask exit
        logger.warning("Worker event-bus reset failed", error=str(e))
    try:
        await shutdown_clients()
    except Exception as e:  # noqa: BLE001 - teardown must not mask exit
        logger.warning("Worker redis shutdown failed", error=str(e))
    logger.info("Worker end")
