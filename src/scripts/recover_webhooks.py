"""CLI script to manually recover stuck webhook deliveries.

Run with: python -m src.scripts.recover_webhooks
"""

import asyncio
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.modules.webhooks.delivery import recover_stuck_deliveries

logger = get_logger("recover_webhooks")

async def main() -> None:
    """Run the recovery job manually."""
    # settings.<component>.<name>, not the legacy flat accessor: the flat
    # form still resolves through Settings.__getattr__ but is a deprecated
    # delegation that warns on first use (config/__init__.py:282).
    redis_queue = settings.redis_queue

    if not redis_queue.REDIS_QUEUE_HOST:
        logger.error("REDIS_QUEUE_HOST is not configured")
        return

    redis_settings = RedisSettings(
        host=redis_queue.REDIS_QUEUE_HOST,
        port=redis_queue.REDIS_QUEUE_PORT,
        password=redis_queue.REDIS_QUEUE_PASSWORD.get_secret_value()
        if redis_queue.REDIS_QUEUE_PASSWORD
        else None,
    )

    try:
        redis = await create_pool(redis_settings)
    except Exception as e:
        logger.error("Failed to connect to Redis queue", error=str(e))
        return

    ctx: dict[str, Any] = {"redis": redis}

    logger.info("Starting manual webhook delivery recovery")
    try:
        recovered = await recover_stuck_deliveries(ctx)
        logger.info("Manual webhook recovery completed", recovered=recovered)
    except Exception as e:
        logger.error("Error running webhook recovery", error=str(e))
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
