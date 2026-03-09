"""Email provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import SecretStr


@dataclass
class EmailAttachment:
    """Email attachment dataclass."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class EmailMessage:
    """Email message dataclass."""

    to_email: str
    subject: str
    html_content: str
    from_name: str | None = None
    from_email: str | None = None
    text_content: str | None = None
    reply_to: str | None = None
    attachments: list[EmailAttachment] | None = None


class EmailProvider(ABC):
    """Abstract email provider interface."""

    @abstractmethod
    async def send_email(self, message: EmailMessage) -> bool:
        """
        Send email message.

        Args:
            message: Email message to send

        Returns
        -------
            True if sent successfully
        """
        pass

    @abstractmethod
    async def send_bulk_emails(self, messages: list[EmailMessage]) -> dict:
        """
        Send multiple email messages.

        Args:
            messages: List of email messages

        Returns
        -------
            Dictionary with success/failure counts
        """
        pass


class SMTPProvider(EmailProvider):
    """SMTP email provider implementation."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        use_tls: bool = True,
        from_email: str | None = None,
        from_name: str | None = None,
    ):
        """Initialize SMTP provider."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.from_email = from_email
        self.from_name = from_name

    async def send_email(self, message: EmailMessage) -> bool:
        """Send email via SMTP with circuit breaker protection."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr

        import aiosmtplib

        from src.app.core.logger import get_logger
        from src.app.core.utils.circuit_breaker import email_circuit

        logger = get_logger(__name__)

        # Check circuit breaker before attempting send
        if not email_circuit.is_closed:
            logger.warning(
                "Email circuit breaker open, skipping email to %s",
                message.to_email,
            )
            return False

        try:
            # Prepare message
            msg = MIMEMultipart("alternative")

            from_email = message.from_email or self.from_email or ""
            from_name = message.from_name or self.from_name or ""

            msg["From"] = formataddr((from_name, from_email))
            msg["To"] = message.to_email
            msg["Subject"] = message.subject

            if message.reply_to:
                msg["Reply-To"] = message.reply_to

            # Attach HTML content
            html_part = MIMEText(message.html_content, "html")
            msg.attach(html_part)

            # Attach plain text content if provided
            if message.text_content:
                text_part = MIMEText(message.text_content, "plain")
                msg.attach(text_part)

            # Attach attachments if provided
            if message.attachments:
                for attachment in message.attachments:
                    from email import encoders
                    from email.mime.base import MIMEBase

                    part = MIMEBase(attachment.content_type.split("/")[0], attachment.content_type.split("/")[1])
                    part.set_payload(attachment.content)
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={attachment.filename}")
                    msg.attach(part)

            # Send via async SMTP (non-blocking)
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username if self.username else None,
                password=self.password.get_secret_value() if self.password else None,
                start_tls=self.use_tls,
            )

            # Record success with circuit breaker
            await email_circuit.record_success()
            return True

        except Exception as e:
            # Record failure with circuit breaker
            await email_circuit.record_failure(e)
            logger.error("Failed to send email", error=e)
            return False

    async def send_bulk_emails(self, messages: list[EmailMessage]) -> dict:
        """Send multiple emails via SMTP."""
        success = 0
        failed = 0

        for message in messages:
            if await self.send_email(message):
                success += 1
            else:
                failed += 1

        return {"success": success, "failed": failed}
