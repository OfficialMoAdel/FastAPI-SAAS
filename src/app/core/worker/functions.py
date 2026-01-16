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


# -------- base functions --------
async def startup(ctx: Worker) -> None:
    logging.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    logging.info("Worker end")
