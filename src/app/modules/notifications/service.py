"""Notification service for sending emails with template support."""

import asyncio
from datetime import UTC, datetime, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from uuid import UUID

import aiosmtplib
from jinja2 import FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.json_utils import json_dumps, json_loads

from ...core.config import settings
from ...core.events import EventPriority, event_bus
from ...core.utils.cache import client as redis_client
from ...core.utils.rate_limit import rate_limiter
from .models import EmailTemplateOverride, Notification, NotificationStatus

logger = get_logger(__name__)

# Initialize Jinja2 environment
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "email"
# Use SandboxedEnvironment to prevent SSTI attacks from user-supplied templates
jinja_env = SandboxedEnvironment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


class NotificationService:
    """Service for sending and tracking email notifications."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize notification service.

        Args:
            db: Database session
        """
        self.db = db

    async def get_template_content(
        self,
        tenant_id: UUID,
        template_name: str,
    ) -> tuple[str, str]:
        """Get email template content (subject and body).

        Checks cache first, then tenant override, then falls back to default file template.

        Args:
            tenant_id: Tenant ID
            template_name: Name of the template (e.g., "welcome", "password_reset")

        Returns
        -------
            Tuple of (subject, html_content)
        """
        # Try cache first to avoid N+1 queries
        cache_key = f"tenant:{tenant_id}:email_template:{template_name}"
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    return tuple(json_loads(cached))
            except Exception as e:
                logger.debug(f"Cache get failed: {e}")

        # Check for tenant override
        query = select(EmailTemplateOverride).where(
            EmailTemplateOverride.tenant_id == tenant_id,
            EmailTemplateOverride.template_name == template_name,
        )
        result = await self.db.execute(query)
        override = result.scalar_one_or_none()

        if override:
            logger.info(
                "using_template_override",
                tenant_id=str(tenant_id),
                template_name=template_name,
            )
            data = (override.subject_override or template_name, override.content)
        else:
            # Use default file template
            try:
                # Non-blocking template loading with thread pool
                template = await asyncio.to_thread(jinja_env.get_template, f"{template_name}.html.jinja2")
                # Default subject from template metadata or template name
                subject = template_name.replace("_", " ").title()
                data = (subject, template.render())
            except Exception as e:
                logger.error(
                    "template_load_failed",
                    template_name=template_name,
                    error=str(e),
                )
                raise ValueError(f"Template '{template_name}' not found")

        # Update cache (1 hour TTL)
        if redis_client:
            try:
                await redis_client.set(cache_key, json_dumps(data, as_str=True), ex=3600)
            except Exception as e:
                logger.debug(f"Cache set failed: {e}")

        return data

    async def render_template(
        self,
        tenant_id: UUID,
        template_name: str,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Render email template with context variables.

        Args:
            tenant_id: Tenant ID
            template_name: Name of the template
            context: Variables to pass to the template

        Returns
        -------
            Tuple of (subject, rendered_html)
        """
        subject, template_content = await self.get_template_content(tenant_id, template_name)

        # Render with context using the sandboxed environment
        # This prevents execution of unsafe code if the template comes from user input
        template = jinja_env.from_string(template_content)
        rendered_html = template.render(**context)

        return subject, rendered_html

    async def send_email(
        self,
        tenant_id: UUID,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: int | None = None,
    ) -> Notification:
        """Send an email notification.

        This method queues the email for async delivery via ARQ.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns
        -------
            Notification record
        """
        # Check user preferences if user_id is provided
        if user_id:
            should_send = await self.should_send_notification(user_id, template_name)
            if not should_send:
                logger.info(
                    "notification_skipped_by_user_preferences",
                    user_id=user_id,
                    template_name=template_name,
                )
                # Create notification record with skipped status
                notification = Notification(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    template_name=template_name,
                    to_email=to_email,
                    subject="",
                    status=NotificationStatus.FAILED,
                )
                notification.error_message = "Skipped by user preferences"
                self.db.add(notification)
                await self.db.commit()
                await self.db.refresh(notification)
                return notification

        # Rate limiting check (T105)
        # 100 emails/minute per tenant
        is_limited_min, _ = await rate_limiter.check_rate_limit(f"notif:min:{tenant_id}", 100, 60)
        if is_limited_min:
            logger.warning("notification_rate_limit_exceeded_min", tenant_id=str(tenant_id))
            raise ValueError("Rate limit exceeded: 100 emails per minute")

        # 1000 emails/hour per tenant
        is_limited_hour, _ = await rate_limiter.check_rate_limit(f"notif:hour:{tenant_id}", 1000, 3600)
        if is_limited_hour:
            logger.warning("notification_rate_limit_exceeded_hour", tenant_id=str(tenant_id))
            raise ValueError("Rate limit exceeded: 1000 emails per hour")

        # Render template
        subject, html_content = await self.render_template(tenant_id, template_name, context)

        # Create notification record
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            template_name=template_name,
            to_email=to_email,
            subject=subject,
            status=NotificationStatus.PENDING,
        )
        self.db.add(notification)
        await self.db.commit()
        await self.db.refresh(notification)

        # Publish event for async delivery
        await event_bus.publish(
            "notification.send",
            {
                "notification_id": str(notification.id),
                "to_email": to_email,
                "subject": subject,
                "html_content": html_content,
            },
            priority=EventPriority.NORMAL,
        )

        logger.info(
            "email_queued",
            notification_id=str(notification.id),
            to_email=to_email,
            template=template_name,
        )

        return notification

    async def mark_sent(self, notification_id: UUID) -> None:
        """Mark notification as sent.

        Args:
            notification_id: Notification ID
        """
        notification = await self.db.get(Notification, notification_id)
        if notification:
            notification.status = NotificationStatus.SENT
            notification.sent_at = datetime.now(UTC)
            await self.db.commit()

    async def mark_failed(self, notification_id: UUID, error_message: str) -> None:
        """Mark notification as failed.

        Args:
            notification_id: Notification ID
            error_message: Error message
        """
        notification = await self.db.get(Notification, notification_id)
        if notification:
            notification.status = NotificationStatus.FAILED
            notification.error_message = error_message
            await self.db.commit()

    async def get_notifications(
        self,
        tenant_id: UUID,
        user_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        """Get notification history.

        Args:
            tenant_id: Tenant ID
            user_id: Optional user filter
            status: Optional status filter
            limit: Maximum results
            offset: Offset for pagination

        Returns
        -------
            List of notifications
        """
        query = select(Notification).where(Notification.tenant_id == tenant_id)

        if user_id:
            query = query.where(Notification.user_id == user_id)
        if status:
            query = query.where(Notification.status == status)

        query = query.order_by(Notification.created_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def should_send_notification(
        self,
        user_id: int,
        notification_type: str,
        current_time: datetime | None = None,
    ) -> bool:
        """Check if a notification should be sent based on user preferences.

        Args:
            user_id: User ID to check preferences for
            notification_type: Type of notification (e.g., "welcome", "password_reset")
            current_time: Current time for quiet hours check (defaults to now)

        Returns
        -------
            True if notification should be sent, False otherwise
        """
        from ...modules.user_preferences.models import UserPreferences

        now = current_time or datetime.now(UTC)

        # Fetch user preferences
        query = select(UserPreferences).where(UserPreferences.user_id == user_id)
        result = await self.db.execute(query)
        prefs = result.scalar_one_or_none()

        # If no preferences exist, default to sending
        if not prefs:
            return True

        # Check if email notifications are enabled
        if not prefs.email_notifications_enabled:
            logger.debug(
                "email_notifications_disabled",
                user_id=user_id,
                notification_type=notification_type,
            )
            return False

        # Check if this notification type is disabled
        if notification_type in prefs.disabled_notification_types:
            logger.debug(
                "notification_type_disabled",
                user_id=user_id,
                notification_type=notification_type,
            )
            return False

        # Check quiet hours
        if prefs.quiet_hours_enabled:
            if prefs.quiet_hours_start and prefs.quiet_hours_end:
                try:
                    start_time = time.fromisoformat(prefs.quiet_hours_start)
                    end_time = time.fromisoformat(prefs.quiet_hours_end)
                    current_time_only = now.time()

                    if start_time < end_time:
                        # Same day range (e.g., 22:00 - 07:00)
                        in_quiet_hours = start_time <= current_time_only < end_time
                    else:
                        # Cross-day range (e.g., 22:00 - 07:00)
                        in_quiet_hours = current_time_only >= start_time or current_time_only < end_time

                    if in_quiet_hours:
                        logger.debug(
                            "quiet_hours_active",
                            user_id=user_id,
                            notification_type=notification_type,
                            quiet_hours_start=prefs.quiet_hours_start,
                            quiet_hours_end=prefs.quiet_hours_end,
                        )
                        return False
                except ValueError as e:
                    logger.warning(
                        "invalid_quiet_hours_format",
                        user_id=user_id,
                        start=prefs.quiet_hours_start,
                        end=prefs.quiet_hours_end,
                        error=str(e),
                    )

        return True


# ARQ task for actual email delivery
async def send_email_task(
    ctx: dict[str, Any],
    notification_id: str,
    to_email: str,
    subject: str,
    html_content: str,
) -> None:
    """ARQ worker task to send emails via SMTP.

    Args:
        ctx: ARQ context
        notification_id: Notification record ID
        to_email: Recipient email
        subject: Email subject
        html_content: HTML body content
    """
    from ...core.db.database import async_get_db

    async with async_get_db() as db:
        try:
            service = NotificationService(db)

            # Create SMTP message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.EMAIL_FROM_ADDRESS
            msg["To"] = to_email

            # Attach HTML content
            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)

            # Send via async SMTP (non-blocking)
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER if settings.SMTP_USER else None,
                password=settings.SMTP_PASSWORD if settings.SMTP_PASSWORD else None,
                start_tls=settings.SMTP_USE_TLS,
            )

            # Mark as sent
            await service.mark_sent(UUID(notification_id))

            logger.info("email_sent_success", notification_id=notification_id, to_email=to_email)

        except Exception as e:
            logger.error(
                "email_send_failed",
                notification_id=notification_id,
                to_email=to_email,
                error=str(e),
                exc_info=True,
            )

            # Mark as failed
            await service.mark_failed(UUID(notification_id), str(e))
            raise  # Re-raise for ARQ retry


# Event handler for notification.send events
@event_bus.subscribe("notification.send")
async def handle_notification_send_event(data: dict[str, Any]) -> None:
    """Handle notification send events by queuing email delivery.

    This handler receives events published by send_email() and other services,
    then enqueues the actual email delivery via ARQ background worker.

    Args:
        data: Event payload with notification details:
            - notification_id: ID of the notification record
            - to_email: Recipient email address
            - subject: Email subject line
            - html_content: Rendered HTML body
    """
    from ...core.utils.queue import pool as arq_pool

    notification_id = data.get("notification_id")
    to_email = data.get("to_email")
    subject = data.get("subject")
    html_content = data.get("html_content")

    if not all([notification_id, to_email, subject, html_content]):
        logger.warning(
            "notification_send_event_missing_data",
            notification_id=notification_id,
            has_to_email=bool(to_email),
            has_subject=bool(subject),
            has_html_content=bool(html_content),
        )
        return

    # Enqueue the email delivery task via ARQ
    if arq_pool:
        try:
            await arq_pool.enqueue_job(
                "send_email_task",
                notification_id,
                to_email,
                subject,
                html_content,
            )
            logger.debug(
                "email_task_enqueued",
                notification_id=notification_id,
                to_email=to_email,
            )
        except Exception as e:
            logger.error(
                "failed_to_enqueue_email_task",
                notification_id=notification_id,
                error=str(e),
            )
    else:
        logger.warning(
            "arq_queue_not_available",
            notification_id=notification_id,
            to_email=to_email,
        )
