"""
ARQ Background Tasks for Core Services.

Handles:
- Email sending with retry logic
- Webhook delivery with exponential backoff
- Event processing from event bus
"""

import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from uuid import UUID

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from src.app.core.config import settings
from src.app.core.db.database import async_sessionmaker
from src.app.modules.notifications.models import Notification, NotificationStatus
from src.app.modules.webhooks.models import DeliveryStatus, WebhookDelivery
from src.app.modules.webhooks.service import WebhookService

logger = logging.getLogger(__name__)

# Jinja2 environment for email templates
jinja_env = Environment(
    loader=FileSystemLoader("src/app/templates/email"),
    autoescape=select_autoescape(["html", "xml"]),
)

# Webhook retry delays (seconds): 1m, 5m, 15m, 30m, 1h
WEBHOOK_RETRY_DELAYS = [60, 300, 900, 1800, 3600]


async def send_email_task(
    ctx: dict[str, Any],
    notification_id: str,
) -> dict[str, Any]:
    """
    Background task to send email notification.

    Args:
        ctx: ARQ context with Redis pool
        notification_id: UUID of the notification to send

    Returns:
        Result dictionary with status
    """
    async with async_sessionmaker() as db:
        try:
            # Get notification
            result = await db.execute(select(Notification).where(Notification.id == UUID(notification_id)))
            notification = result.scalar_one_or_none()

            if not notification:
                logger.error(f"Notification {notification_id} not found")
                return {"status": "error", "message": "Notification not found"}

            if notification.status == NotificationStatus.SENT:
                logger.info(f"Notification {notification_id} already sent")
                return {"status": "skipped", "message": "Already sent"}

            # Render email template
            from src.app.modules.notifications.service import NotificationService

            service = NotificationService()

            html_content = await service.render_template(
                db=db,
                tenant_id=notification.tenant_id,
                template_name=notification.template_name,
                context=notification.context or {},
            )

            # Create email message
            message = EmailMessage()
            message["From"] = settings.SMTP_FROM_EMAIL
            message["To"] = notification.to_email
            message["Subject"] = notification.subject
            message.set_content(html_content, subtype="html")

            # Send via SMTP
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD,
                use_tls=settings.SMTP_USE_TLS,
            )

            # Update notification status
            notification.status = NotificationStatus.SENT
            notification.sent_at = datetime.now(UTC)
            await db.commit()

            logger.info(f"Email sent successfully: {notification_id}")
            return {"status": "success", "notification_id": notification_id}

        except Exception as e:
            logger.error(f"Failed to send email {notification_id}: {e}", exc_info=True)

            # Update notification with error
            if notification:
                notification.status = NotificationStatus.FAILED
                notification.error_message = str(e)
                await db.commit()

            return {"status": "error", "message": str(e)}


async def deliver_webhook_task(
    ctx: dict[str, Any],
    delivery_id: str,
    attempt: int = 0,
) -> dict[str, Any]:
    """
    Background task to deliver webhook with retry logic.

    Args:
        ctx: ARQ context with Redis pool
        delivery_id: UUID of the webhook delivery
        attempt: Current attempt number (0-based)

    Returns:
        Result dictionary with status
    """
    async with async_sessionmaker() as db:
        try:
            # Get webhook delivery
            result = await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == UUID(delivery_id)))
            delivery = result.scalar_one_or_none()

            if not delivery:
                logger.error(f"Webhook delivery {delivery_id} not found")
                return {"status": "error", "message": "Delivery not found"}

            if delivery.status == DeliveryStatus.DELIVERED:
                logger.info(f"Webhook delivery {delivery_id} already delivered")
                return {"status": "skipped", "message": "Already delivered"}

            # Attempt delivery
            service = WebhookService()
            success = await service.attempt_delivery(db=db, delivery=delivery)

            if success:
                logger.info(f"Webhook delivered successfully: {delivery_id}")
                return {"status": "success", "delivery_id": delivery_id}

            # Retry logic
            if attempt < len(WEBHOOK_RETRY_DELAYS):
                # Schedule retry
                delay = WEBHOOK_RETRY_DELAYS[attempt]
                delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                await db.commit()

                # Enqueue retry task
                await ctx["redis"].enqueue_job(
                    "deliver_webhook_task",
                    delivery_id,
                    attempt + 1,
                    _defer_by=delay,
                )

                logger.info(f"Webhook delivery {delivery_id} scheduled for retry {attempt + 1} in {delay}s")
                return {
                    "status": "retry_scheduled",
                    "attempt": attempt + 1,
                    "delay": delay,
                }
            else:
                # Max retries exceeded
                delivery.status = DeliveryStatus.FAILED
                await db.commit()

                logger.error(f"Webhook delivery {delivery_id} failed after all retries")
                return {"status": "failed", "message": "Max retries exceeded"}

        except Exception as e:
            logger.error(
                f"Error in webhook delivery task {delivery_id}: {e}",
                exc_info=True,
            )
            return {"status": "error", "message": str(e)}


async def process_event_task(
    ctx: dict[str, Any],
    event_type: str,
    event_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Background task to process events from event bus.

    Used for non-critical events that can be processed asynchronously.

    Args:
        ctx: ARQ context
        event_type: Type of event (e.g., "user.created")
        event_data: Event payload

    Returns:
        Result dictionary
    """
    try:
        logger.info(f"Processing event: {event_type}")

        # Import event bus to get handlers
        from src.app.core.events import event_bus

        # Execute handlers
        await event_bus._execute_handlers(event_type, event_data)

        return {"status": "success", "event_type": event_type}

    except Exception as e:
        logger.error(f"Error processing event {event_type}: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


# ARQ task configuration
class WorkerSettings:
    """ARQ worker settings for core services tasks."""

    functions = [
        send_email_task,
        deliver_webhook_task,
        process_event_task,
    ]

    redis_settings = {
        "host": settings.REDIS_HOST,
        "port": settings.REDIS_PORT,
        "database": settings.REDIS_DB,
    }

    # Task execution settings
    max_jobs = 10
    job_timeout = 300  # 5 minutes
    keep_result = 3600  # Keep results for 1 hour
