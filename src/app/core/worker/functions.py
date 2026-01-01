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


async def deliver_webhook_task(ctx: Worker, delivery_id: UUID) -> str:
    """Background task to deliver a webhook."""
    async with local_session() as db:
        service = WebhookService(db)
        try:
            await service.deliver_webhook(delivery_id)
            return f"Webhook {delivery_id} delivered."
        except Exception as e:
            logging.error(f"Failed to deliver webhook {delivery_id}: {e}")
            raise e


# -------- base functions --------
async def startup(ctx: Worker) -> None:
    logging.info("Worker Started")


async def shutdown(ctx: Worker) -> None:
    logging.info("Worker end")
