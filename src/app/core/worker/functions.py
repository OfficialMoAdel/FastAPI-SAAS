import asyncio
import logging
import time
from uuid import UUID

from arq.worker import Worker
from sqlalchemy import text

from src.app.core.db.database import local_session
from src.app.core.job_monitoring import get_job_monitor
from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.modules.webhooks.service import WebhookService

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


# -------- background tasks --------
async def sample_background_task(ctx: Worker, name: str) -> str:
    monitor = get_job_monitor()
    job_id = ctx.get("job_id", "unknown") if isinstance(ctx, dict) else "unknown"
    func_name = "sample_background_task"

    await monitor.record_job_start(job_id, func_name)
    start_time = time.perf_counter()

    try:
        await asyncio.sleep(5)
        result = f"Task {name} is complete!"

        duration = time.perf_counter() - start_time
        await monitor.record_job_success(job_id, func_name, duration)
        return result
    except Exception as e:
        await monitor.record_job_failure(job_id, func_name, str(e))
        raise e


async def deliver_webhook_task(ctx: Worker, delivery_id: UUID | str) -> str:
    """Background task to deliver a webhook.

    Note: delivery_id may be str after orjson deserialization (UUID → str → str).
    """
    monitor = get_job_monitor()
    job_id = ctx.get("job_id", "unknown") if isinstance(ctx, dict) else "unknown"
    func_name = "deliver_webhook_task"

    await monitor.record_job_start(job_id, func_name)
    start_time = time.perf_counter()

    try:
        # Convert string to UUID if needed (orjson deserializes UUID as string)
        if isinstance(delivery_id, str):
            delivery_id = UUID(delivery_id)

        # First, get tenant_id from delivery record (bypass needed for lookup)
        async with job_bypass_context("Fetching tenant_id for webhook delivery"):
            async with local_session() as db:
                db_result = await db.execute(
                    text("SELECT tenant_id FROM webhook_deliveries WHERE id = :id"),
                    {"id": str(delivery_id)},
                )
                row = db_result.fetchone()
                if not row:
                    raise ValueError(f"Webhook delivery {delivery_id} not found")
                tenant_id = row[0]

        # Now process with proper tenant context
        async with job_tenant_context(tenant_id):
            async with local_session() as db:
                service = WebhookService(db)
                try:
                    await service.deliver_webhook(delivery_id)
                    result = f"Webhook {delivery_id} delivered."

                    duration = time.perf_counter() - start_time
                    await monitor.record_job_success(job_id, func_name, duration)
                    return result
                except Exception as e:
                    logging.error(f"Failed to deliver webhook {delivery_id}: {e}")
                    raise e
    except Exception as e:
        await monitor.record_job_failure(job_id, func_name, str(e))
        raise e


async def migrate_sessions_task(ctx: Worker) -> str:
    """Background task to migrate sessions to hash format."""
    from src.app.core.security.session_manager import session_manager

    monitor = get_job_monitor()
    job_id = ctx.get("job_id", "unknown") if isinstance(ctx, dict) else "unknown"
    func_name = "migrate_sessions_task"

    await monitor.record_job_start(job_id, func_name)
    start_time = time.perf_counter()

    logging.info("Starting session migration task")
    try:
        count = await session_manager.migrate_to_hash()
        result = f"Migrated {count} users to hash format."

        duration = time.perf_counter() - start_time
        await monitor.record_job_success(job_id, func_name, duration)
        return result
    except Exception as e:
        logging.error(f"Session migration failed: {e}")
        await monitor.record_job_failure(job_id, func_name, str(e))
        raise e


async def flush_audit_logs_task(ctx: dict) -> str:
    """Periodic task to flush buffered audit logs to database.

    Should be scheduled as a cron job (every 10-60 seconds).
    Drains the Redis buffer completely in batches of 100.

    System task - requires RLS bypass.
    """
    from src.app.modules.audit_logs.service import (
        AUDIT_LOG_BATCH_SIZE,
        flush_audit_log_buffer,
    )

    monitor = get_job_monitor()
    job_id = ctx.get("job_id", "unknown") if isinstance(ctx, dict) else "unknown"
    func_name = "flush_audit_logs_task"

    await monitor.record_job_start(job_id, func_name)
    start_time = time.perf_counter()

    logging.info("Starting audit log flush task")
    total = 0

    try:
        async with job_bypass_context("Flushing audit logs to database"):
            try:
                # Drain buffer completely
                while True:
                    flushed = await flush_audit_log_buffer()
                    total += flushed
                    if flushed < AUDIT_LOG_BATCH_SIZE:
                        break

                if total > 0:
                    logging.info(f"Flushed {total} audit logs to database")
                result = f"Flushed {total} audit logs."

                duration = time.perf_counter() - start_time
                await monitor.record_job_success(job_id, func_name, duration)
                return result
            except Exception as e:
                logging.error(f"Audit log flush failed: {e}")
                raise e
    except Exception as e:
        await monitor.record_job_failure(job_id, func_name, str(e))
        raise e


async def sync_bloom_filter_task(ctx: dict) -> str:
    """Periodic task to sync Bloom filter from Redis.

    Should be scheduled as a cron job (every 5 minutes).
    Ensures all instances have consistent Bloom filter state for token blacklist.

    System task - requires RLS bypass.
    """
    from src.app.core.security.bloom_filter import sync_bloom_filter_from_redis

    monitor = get_job_monitor()
    job_id = ctx.get("job_id", "unknown") if isinstance(ctx, dict) else "unknown"
    func_name = "sync_bloom_filter_task"

    await monitor.record_job_start(job_id, func_name)
    start_time = time.perf_counter()

    logging.info("Starting Bloom filter sync task")

    try:
        async with job_bypass_context("Syncing bloom filter from Redis"):
            try:
                count = await sync_bloom_filter_from_redis()
                if count > 0:
                    logging.info(f"Synced {count} tokens to Bloom filter")
                result = f"Synced {count} tokens to Bloom filter."

                duration = time.perf_counter() - start_time
                await monitor.record_job_success(job_id, func_name, duration)
                return result
            except Exception as e:
                logging.error(f"Bloom filter sync failed: {e}")
                raise e
    except Exception as e:
        await monitor.record_job_failure(job_id, func_name, str(e))
        raise e


# -------- base functions --------
async def startup(ctx: Worker) -> None:
    logging.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    logging.info("Worker end")
