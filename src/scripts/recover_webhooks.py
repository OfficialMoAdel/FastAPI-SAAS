"""CLI script to manually recover stuck webhook deliveries.

Run with: python -m src.scripts.recover_webhooks
"""

import asyncio
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.modules.webhooks.recovery import recover_stuck_deliveries

logger = get_logger("recover_webhooks")

async def main() -> None:
    """Run the recovery job manually."""
    redis_settings = RedisSettings(
        host=settings.REDIS_QUEUE_HOST,
        port=settings.REDIS_QUEUE_PORT,
        password=settings.REDIS_QUEUE_PASSWORD.get_secret_value() if settings.REDIS_QUEUE_PASSWORD else None,
    )

    redis = await create_pool(redis_settings)
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
