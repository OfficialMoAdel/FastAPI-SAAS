import asyncio
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


@monitor_job("sync_bloom_filter_task")
async def sync_bloom_filter_task(ctx: dict) -> str:
    """Periodic task to sync Bloom filter from Redis.

    Should be scheduled as a cron job (every 5 minutes).
    Ensures all instances have consistent Bloom filter state for token blacklist.

    System task - requires RLS bypass.
    """
    from src.app.core.security.bloom_filter import sync_bloom_filter_from_redis

    logger.info("Starting Bloom filter sync task")

    async with job_bypass_context("Syncing bloom filter from Redis"):
        try:
            count = await sync_bloom_filter_from_redis()
            if count > 0:
                logger.info(f"Synced {count} tokens to Bloom filter")
            return f"Synced {count} tokens to Bloom filter."
        except Exception as e:
            logger.error(f"Bloom filter sync failed: {e}")
            raise


# -------- base functions --------
async def startup(ctx: Worker) -> None:
    logger.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    logger.info("Worker end")
