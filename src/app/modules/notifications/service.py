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

from src.app.core.config import settings
from src.app.core.events import EventPriority, event_bus
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.utils.cache import client as redis_client
from src.app.core.utils.rate_limit import rate_limiter
from src.app.modules.notifications.models import EmailTemplateOverride, Notification, NotificationStatus

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
                logger.debug("Cache get failed", error=e)

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
            # Use default file template - return the RAW TEMPLATE SOURCE, not rendered output.
            # BUG FIX: Previously called template.render() here without any context, which
            # pre-renders the template with empty variables and destroys the Jinja2 syntax
            # (e.g., {{ user.name }} becomes empty string). The raw source must be stored
            # so that render_template() can fill variables later.
            try:
                # Non-blocking template loading with thread pool (validates the template exists)
                await asyncio.to_thread(jinja_env.get_template, f"{template_name}.html.jinja2")
                # Default subject from template name
                subject = template_name.replace("_", " ").title()
                # Return the raw Jinja2 source via the loader.
                # get_source() returns (source_str, filename, uptodate_fn) — we take [0].
                # jinja_env.loader is always a FileSystemLoader (set at module init), never None.
                assert jinja_env.loader is not None
                raw_source, _, _ = await asyncio.to_thread(
                    jinja_env.loader.get_source, jinja_env, f"{template_name}.html.jinja2"
                )
                data = (subject, raw_source)
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
                logger.debug("Cache set failed", error=e)

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
                # BUG FIX: Skipped notifications should NOT be stored as FAILED.
                # FAILED implies an error occurred; PENDING is a safer neutral status
                # that makes the intent clear via the error_message field.
                notification = Notification(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    template_name=template_name,
                    to_email=to_email,
                    subject="",
                    status=NotificationStatus.PENDING,
                )
                notification.error_message = "Skipped: user preference opt-out"
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
                "tenant_id": str(tenant_id),
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
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[Notification]]:
        """Get notification history.

        Args:
            tenant_id: Tenant ID
            user_id: Optional user filter
            status: Optional status filter
            start_date: Optional from date filter
            end_date: Optional to date filter
            limit: Maximum results
            offset: Offset for pagination

        Returns
        -------
            Tuple of (total_count, List of notifications)
        """
        from sqlalchemy import func

        query = select(Notification).where(Notification.tenant_id == tenant_id)

        if user_id:
            query = query.where(Notification.user_id == user_id)
        if status:
            query = query.where(Notification.status == status)
        if start_date:
            query = query.where(Notification.created_at >= start_date)
        if end_date:
            query = query.where(Notification.created_at <= end_date)

        query = query.order_by(Notification.created_at.desc())

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return total, list(result.scalars().all())

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
        from src.app.modules.user_preferences.models import UserPreferences

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

        disabled_types = prefs.disabled_notification_types or []
        if notification_type in disabled_types:
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

    async def list_template_overrides(
        self, tenant_id: UUID, limit: int = 100, offset: int = 0
    ) -> list[EmailTemplateOverride]:
        """List all email template overrides for a tenant"""
        query = (
            select(EmailTemplateOverride)
            .where(EmailTemplateOverride.tenant_id == tenant_id)
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_template_override(
        self, tenant_id: UUID, template_name: str, subject_override: str | None, content: str
    ) -> EmailTemplateOverride:
        """Create or update an email template override"""
        query = select(EmailTemplateOverride).where(
            EmailTemplateOverride.tenant_id == tenant_id,
            EmailTemplateOverride.template_name == template_name,
        )
        result = await self.db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing
            existing.subject_override = subject_override
            existing.content = content
            await self.db.commit()
            await self.db.refresh(existing)

            # Invalidate cache
            if redis_client:
                await redis_client.delete(f"tenant:{tenant_id}:email_template:{template_name}")

            return existing

        # Create new
        override = EmailTemplateOverride(
            tenant_id=tenant_id,
            template_name=template_name,
            subject_override=subject_override,
            content=content,
        )
        self.db.add(override)
        await self.db.commit()
        await self.db.refresh(override)

        # Invalidate cache
        if redis_client:
            await redis_client.delete(f"tenant:{tenant_id}:email_template:{template_name}")

        return override

    async def delete_template_override(self, tenant_id: UUID, template_name: str) -> bool:
        """Delete an email template override returns True if deleted, False if not found"""
        query = select(EmailTemplateOverride).where(
            EmailTemplateOverride.tenant_id == tenant_id,
            EmailTemplateOverride.template_name == template_name,
        )
        result = await self.db.execute(query)
        override = result.scalar_one_or_none()

        if not override:
            return False

        await self.db.delete(override)
        await self.db.commit()

        # Invalidate cache
        if redis_client:
            await redis_client.delete(f"tenant:{tenant_id}:email_template:{template_name}")

        return True


# ARQ task for actual email delivery
async def send_email_task(
    ctx: dict[str, Any],
    notification_id: str,
    to_email: str,
    subject: str,
    html_content: str,
    tenant_id: str | None = None,
) -> None:
    """ARQ worker task to send emails via SMTP.

    Args:
        ctx: ARQ context
        notification_id: Notification record ID
        to_email: Recipient email
        subject: Email subject
        html_content: HTML body content
    """
    from src.app.core.db.database import async_session_maker
    from src.app.core.db.rls import rls_context

    async with async_session_maker() as db:
        # BUG FIX: Previously, the rls_context was exited immediately after creating
        # the service object (the entire context block contained only `service = ...`).
        # This meant mark_sent() and mark_failed() DB writes happened WITHOUT tenant
        # isolation, which violates RLS and could cause cross-tenant data leakage.
        # Fix: wrap ALL DB operations (create, SMTP send, mark) inside the context.
        async def _run_with_context() -> None:
            service = NotificationService(db)

            # Create SMTP message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.EMAIL_FROM_ADDRESS
            msg["To"] = to_email

            # Attach HTML content
            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)

            try:
                # Send via async SMTP (non-blocking)
                await aiosmtplib.send(
                    msg,
                    hostname=settings.SMTP_HOST,
                    port=settings.SMTP_PORT,
                    username=settings.SMTP_USER if settings.SMTP_USER else None,
                    password=settings.SMTP_PASSWORD.get_secret_value() if settings.SMTP_PASSWORD else None,
                    start_tls=settings.SMTP_USE_TLS,
                )

                # Mark as sent (inside context = RLS enforced)
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
                # Mark as failed (inside context = RLS enforced)
                await service.mark_failed(UUID(notification_id), str(e))
                raise  # Re-raise for ARQ retry

        try:
            if tenant_id:
                async with rls_context(db, tenant_id):
                    await _run_with_context()
            else:
                await _run_with_context()
        except Exception:
            raise  # Already logged inside _run_with_context


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
    from src.app.core.utils.queue import pool as arq_pool

    notification_id = data.get("notification_id")
    to_email = data.get("to_email")
    subject = data.get("subject")
    html_content = data.get("html_content")
    tenant_id = data.get("tenant_id")

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
                tenant_id,
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
            # BUG FIX: Mark notification as FAILED so it doesn't stay stuck in PENDING
            await _mark_notification_failed(notification_id, tenant_id, f"Enqueue error: {e}")
    else:
        logger.warning(
            "arq_queue_not_available",
            notification_id=notification_id,
            to_email=to_email,
        )
        # BUG FIX: Mark notification as FAILED so it doesn't stay stuck in PENDING
        await _mark_notification_failed(notification_id, tenant_id, "ARQ queue not available")


async def _mark_notification_failed(notification_id: str | None, tenant_id: str | None, reason: str) -> None:
    """Mark a notification as failed in the database."""
    if not notification_id:
        return
    try:
        from uuid import UUID

        from src.app.core.db.database import local_session
        from src.app.core.db.rls import rls_context
        async with local_session() as db:
            ctx = rls_context(db, tenant_id) if tenant_id else None
            if ctx:
                async with ctx:
                    service = NotificationService(db)
                    await service.mark_failed(UUID(notification_id), reason)
            else:
                service = NotificationService(db)
                await service.mark_failed(UUID(notification_id), reason)
    except Exception as e:
        logger.error("failed_to_mark_notification_failed", notification_id=notification_id, error=str(e))
