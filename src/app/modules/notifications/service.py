"""Notification service for sending emails with template support."""

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.events import EventPriority, event_bus
from src.app.modules.notifications.models import EmailTemplateOverride, Notification

logger = get_logger(__name__)

# Initialize Jinja2 environment
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "email"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


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

        Checks for tenant override first, falls back to default file template.

        Args:
            tenant_id: Tenant ID
            template_name: Name of the template (e.g., "welcome", "password_reset")

        Returns:
            Tuple of (subject, html_content)
        """
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
            return override.subject_override or template_name, override.content

        # Use default file template
        try:
            template = jinja_env.get_template(f"{template_name}.html.jinja2")
            # Default subject from template metadata or template name
            subject = template_name.replace("_", " ").title()
            return subject, template.render()
        except Exception as e:
            logger.error(
                "template_load_failed",
                template_name=template_name,
                error=str(e),
            )
            raise ValueError(f"Template '{template_name}' not found")

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

        Returns:
            Tuple of (subject, rendered_html)
        """
        subject, template_content = await self.get_template_content(tenant_id, template_name)

        # Render with context
        template = Template(template_content)
        rendered_html = template.render(**context)

        return subject, rendered_html

    async def send_email(
        self,
        tenant_id: UUID,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: Optional[UUID] = None,
    ) -> Notification:
        """Send an email notification.

        This method queues the email for async delivery via ARQ.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns:
            Notification record
        """
        # Render template
        subject, html_content = await self.render_template(tenant_id, template_name, context)

        # Create notification record
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            template_name=template_name,
            to_email=to_email,
            subject=subject,
            status="pending",
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
            notification.status = "sent"
            notification.sent_at = datetime.utcnow()
            await self.db.commit()

    async def mark_failed(self, notification_id: UUID, error_message: str) -> None:
        """Mark notification as failed.

        Args:
            notification_id: Notification ID
            error_message: Error message
        """
        notification = await self.db.get(Notification, notification_id)
        if notification:
            notification.status = "failed"
            notification.error_message = error_message
            await self.db.commit()

    async def get_notifications(
        self,
        tenant_id: UUID,
        user_id: Optional[UUID] = None,
        status: Optional[str] = None,
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

        Returns:
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
    from src.app.core.db.database import async_get_db

    async for db in async_get_db():
        try:
            service = NotificationService(db)

            # Create SMTP message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM_EMAIL
            msg["To"] = to_email

            # Attach HTML content
            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)

            # Send via SMTP
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                if settings.SMTP_TLS:
                    server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

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

        finally:
            await db.close()
            break


# Event handler for notification.send events
@event_bus.subscribe("notification.send")
async def handle_notification_send_event(data: dict[str, Any]) -> None:
    """Handle notification send events by queuing email delivery.

    Args:
        data: Event payload with notification details
    """
    # This will be handled by ARQ queue via event bus
    # The actual work is done by send_email_task
    pass
