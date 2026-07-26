"""Email sending and logging utilities."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr

from src.app.core.config import settings
from src.app.core.db.database import local_session
from src.app.core.logger import get_logger
from src.app.modules.notifications.email_provider import EmailMessage, EmailProvider, SMTPProvider
from src.app.modules.notifications.models import Notification, NotificationStatus

logger = get_logger(__name__)


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
                host=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or "",
                password=settings.SMTP_PASSWORD or SecretStr(""),
                use_tls=settings.SMTP_USE_TLS,
                from_email=settings.EMAIL_FROM_ADDRESS,
                from_name=settings.EMAIL_FROM_NAME,
            )

        return self._provider

    async def send_email(
        self,
        tenant_id: int,
        user_id: int | None,
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

        Returns
        -------
            True if sent successfully
        """
        # Prepare message
        message = EmailMessage(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            from_email=settings.EMAIL_FROM_ADDRESS,
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
            logger.error("Failed to send email", error=e)

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
        tenant_id: int,
        user_id: int | None,
        template_name: str,
        to_email: str,
        subject: str,
        status: NotificationStatus,
        error_message: str | None = None,
    ):
        """Log notification to database."""
        async with self._session_maker() as session:
            notification = Notification(
                tenant_id=UUID(str(tenant_id)) if isinstance(tenant_id, int) else tenant_id,
                user_id=user_id,
                template_name=template_name,
                to_email=to_email,
                subject=subject,
                status=status,
            )
            # Set init=False fields after creation
            if status == NotificationStatus.SENT:
                notification.sent_at = datetime.now(UTC)
            if error_message:
                notification.error_message = error_message

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

        Returns
        -------
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
                from_email=settings.EMAIL_FROM_ADDRESS,
                from_name=settings.EMAIL_FROM_NAME,
            )
            email_messages.append(email_message)

        # Send emails - request per-message results when supported
        results = await provider.send_bulk_emails(email_messages)

        # Per-message results list: True = sent, False = failed.
        # Providers that support per-message status return a "results" list;
        # older providers return only aggregate counts (success/failed).
        per_message_results: list[bool] | None = results.get("results")

        total_success_remaining: int = results.get("success", 0)

        # Log all notifications
        for i, (msg_data, email_message) in enumerate(zip(messages, email_messages, strict=False)):
            if per_message_results is not None:
                # Preferred path: provider returned per-message status
                success = bool(per_message_results[i]) if i < len(per_message_results) else False
            else:
                # Fallback: distribute aggregate count across messages in order
                # (first N=success_count messages are considered successful)
                success = total_success_remaining > 0
                total_success_remaining -= 1 if success else 0

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
