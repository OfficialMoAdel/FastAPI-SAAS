from datetime import UTC, datetime, timedelta
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import select

from src.app.core.config import settings
from src.app.core.db.database import get_db_context
from src.app.core.db.rls import bypass_rls
from src.app.core.logger import get_logger
from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus

logger = get_logger(__name__)

async def recover_stuck_deliveries(ctx: dict[str, Any]) -> int:
    """ARQ periodic job: find PENDING deliveries past their next_retry_at and re-enqueue."""
    recovered = 0
    buffer_minutes = settings.webhooks.WEBHOOK_RECOVERY_BUFFER_MINUTES
    cutoff_time = datetime.now(UTC) - timedelta(minutes=buffer_minutes)

    redis: ArqRedis | None = ctx.get("redis")
    if not redis:
        logger.error("Redis not available in context for webhook recovery")
        return 0

    async with get_db_context() as db:
        async with bypass_rls(db, reason="Webhook recovery job"):
            stmt = select(WebhookDelivery).where(
                WebhookDelivery.status == WebhookDeliveryStatus.PENDING,
                WebhookDelivery.next_retry_at < cutoff_time,
            ).limit(settings.webhooks.WEBHOOK_RECOVERY_BATCH_SIZE).with_for_update(skip_locked=True)

            result = await db.execute(stmt)
            deliveries = result.scalars().all()

            for delivery in deliveries:
                # Re-enqueue via ARQ
                await redis.enqueue_job(
                    "deliver_webhook_task",
                    delivery_id=str(delivery.id),
                    tenant_id=str(delivery.tenant_id),
                    _job_id=f"recover_webhook_{delivery.id}",
                )
                # Update next_retry_at to prevent immediate re-selection if job takes a while
                delivery.next_retry_at = datetime.now(UTC) + timedelta(minutes=10)
                recovered += 1

            if recovered > 0:
                await db.commit()

    if recovered > 0:
        logger.info("Recovered stuck webhook deliveries", count=recovered)

    return recovered
