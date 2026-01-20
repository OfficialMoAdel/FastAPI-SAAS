import asyncio
import logging
from uuid import UUID

from arq.worker import Worker

from src.app.core.db.database import local_session
from src.app.modules.webhooks.service import WebhookService

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


# -------- background tasks --------
async def sample_background_task(ctx: Worker, name: str) -> str:
    await asyncio.sleep(5)
    return f"Task {name} is complete!"


async def deliver_webhook_task(ctx: Worker, delivery_id: UUID | str) -> str:
    """Background task to deliver a webhook.

    Note: delivery_id may be str after orjson deserialization (UUID → str → str).
    """
    # Convert string to UUID if needed (orjson deserializes UUID as string)
    if isinstance(delivery_id, str):
        delivery_id = UUID(delivery_id)

    async with local_session() as db:
        service = WebhookService(db)
        try:
            await service.deliver_webhook(delivery_id)
            return f"Webhook {delivery_id} delivered."
        except Exception as e:
            logging.error(f"Failed to deliver webhook {delivery_id}: {e}")
            raise e


async def migrate_sessions_task(ctx: Worker) -> str:
    """Background task to migrate sessions to hash format."""
    from src.app.core.security.session_manager import session_manager

    logging.info("Starting session migration task")
    try:
        count = await session_manager.migrate_to_hash()
        return f"Migrated {count} users to hash format."
    except Exception as e:
        logging.error(f"Session migration failed: {e}")
        raise e


async def flush_audit_logs_task(ctx: dict) -> str:
    """Periodic task to flush buffered audit logs to database.

    Should be scheduled as a cron job (every 10-60 seconds).
    Drains the Redis buffer completely in batches of 100.
    """
    from src.app.modules.audit_logs.service import (
        AUDIT_LOG_BATCH_SIZE,
        flush_audit_log_buffer,
    )

    logging.info("Starting audit log flush task")
    total = 0

    try:
        # Drain buffer completely
        while True:
            flushed = await flush_audit_log_buffer()
            total += flushed
            if flushed < AUDIT_LOG_BATCH_SIZE:
                break

        if total > 0:
            logging.info(f"Flushed {total} audit logs to database")
        return f"Flushed {total} audit logs."
    except Exception as e:
        logging.error(f"Audit log flush failed: {e}")
        raise e


async def sync_bloom_filter_task(ctx: dict) -> str:
    """Periodic task to sync Bloom filter from Redis.

    Should be scheduled as a cron job (every 5 minutes).
    Ensures all instances have consistent Bloom filter state for token blacklist.
    """
    from src.app.core.security.bloom_filter import sync_bloom_filter_from_redis

    logging.info("Starting Bloom filter sync task")
    try:
        count = await sync_bloom_filter_from_redis()
        if count > 0:
            logging.info(f"Synced {count} tokens to Bloom filter")
        return f"Synced {count} tokens to Bloom filter."
    except Exception as e:
        logging.error(f"Bloom filter sync failed: {e}")
        raise e


# -------- base functions --------
async def startup(ctx: Worker) -> None:
    logging.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    logging.info("Worker end")
