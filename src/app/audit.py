"""Email sending and logging utilities."""

import logging
from datetime import datetime

from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.email_provider import EmailMessage, EmailProvider, SMTPProvider
from src.app.modules.notifications.models import Notification, NotificationStatus

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending and logging emails."""

    def __init__(self):
        """Initialize email service."""
        self._session_maker = local_session
        self._provider: EmailProvider | None = None

    def _get_provider(self) -> EmailProvider:
        """Get email provider instance."""
        if self._provider is None:
            self._provider = SMTPProvider(
                host=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                username=settings.EMAIL_USERNAME,
                password=settings.EMAIL_PASSWORD,
                use_tls=settings.EMAIL_USE_TLS,
                from_email=settings.EMAIL_FROM,
                from_name=settings.EMAIL_FROM_NAME,
            )

        return self._provider

    async def send_email(
        self,
        tenant_id: str,
        user_id: str,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
        template_name: str | None = None,
    ) -> bool:
        """
        Send email and log to database.

        Args:
            tenant_id: Tenant ID
            user_id: User ID
            to_email: Recipient email
            subject: Email subject
            html_content: Email HTML content
            text_content: Optional plain text content
            template_name: Optional template name for tracking

        Returns:
            True if sent successfully
        """
        # Prepare message
        message = EmailMessage(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            from_email=settings.EMAIL_FROM,
            from_name=settings.EMAIL_FROM_NAME,
        )

        # Send email
        try:
            provider = self._get_provider()
            success = await provider.send_email(message)

            # Log result
            await self._log_notification(
                tenant_id=tenant_id,
                user_id=user_id,
                template_name=template_name or "custom",
                to_email=to_email,
                subject=subject,
                status=NotificationStatus.SENT if success else NotificationStatus.FAILED,
            )

            return success

        except Exception as e:
            logger.error(f"Failed to send email: {e}")

            # Log failure
            await self._log_notification(
                tenant_id=tenant_id,
                user_id=user_id,
                template_name=template_name or "custom",
                to_email=to_email,
                subject=subject,
                status=NotificationStatus.FAILED,
                error_message=str(e),
            )

            return False

    async def _log_notification(
        self,
        tenant_id: str,
        user_id: str,
        template_name: str,
        to_email: str,
        subject: str,
        status: NotificationStatus,
        error_message: str | None = None,
    ):
        """Log notification to database."""
        async with self._session_maker() as session:
            notification = Notification(
                tenant_id=tenant_id,
                user_id=user_id,
                template_name=template_name,
                to_email=to_email,
                subject=subject,
                status=status,
                sent_at=datetime.utcnow() if status == NotificationStatus.SENT else None,
                error_message=error_message,
            )

            session.add(notification)
            await session.commit()

    async def send_bulk_emails(self, messages: list[dict]) -> dict:
        """
        Send multiple emails.

        Args:
            messages: List of email dicts with keys:
                - tenant_id: str
                - user_id: str
                - to_email: str
                - subject: str
                - html_content: str
                - text_content: Optional[str]
                - template_name: Optional[str]

        Returns:
            Dictionary with success/failure counts
        """
        provider = self._get_provider()
        email_messages = []

        # Prepare email messages
        for msg_data in messages:
            email_message = EmailMessage(
                to_email=msg_data["to_email"],
                subject=msg_data["subject"],
                html_content=msg_data["html_content"],
                text_content=msg_data.get("text_content"),
                from_email=settings.EMAIL_FROM,
                from_name=settings.EMAIL_FROM_NAME,
            )
            email_messages.append(email_message)

        # Send emails
        results = await provider.send_bulk_emails(email_messages)

        # Log all notifications
        for msg_data, email_message in zip(messages, email_messages):
            # Determine success
            success = results["success"] > 0
            results["success"] -= 1 if success else 0

            await self._log_notification(
                tenant_id=msg_data["tenant_id"],
                user_id=msg_data["user_id"],
                template_name=msg_data.get("template_name", "custom"),
                to_email=msg_data["to_email"],
                subject=msg_data["subject"],
                status=NotificationStatus.SENT if success else NotificationStatus.FAILED,
                error_message=None if success else "Unknown error",
            )

        return results


email_service = EmailService()
