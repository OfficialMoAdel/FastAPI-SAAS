"""Email provider interface and SMTP / InMemory integration clients."""

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
        """Send email message.

        Args:
            message: Email message to send

        Returns:
            True if sent successfully
        """
        pass

    @abstractmethod
    async def send_bulk_emails(self, messages: list[EmailMessage]) -> dict:
        """Send multiple email messages.

        Args:
            messages: List of email messages

        Returns:
            Dictionary with success/failure counts
        """
        pass


class SMTPProvider(EmailProvider):
    """SMTP email provider implementation."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: SecretStr | None = None,
        use_tls: bool = True,
        from_email: str | None = None,
        from_name: str | None = None,
    ) -> None:
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

            # M11 (defense in depth): strip CR/LF from every header value to
            # prevent SMTP header injection via user-influenced fields.
            def _sanitize_header(value: str) -> str:
                return value.replace("\r", "").replace("\n", "")

            msg["From"] = formataddr((_sanitize_header(from_name), _sanitize_header(from_email)))
            msg["To"] = _sanitize_header(message.to_email)
            msg["Subject"] = _sanitize_header(message.subject)

            if message.reply_to:
                msg["Reply-To"] = _sanitize_header(message.reply_to)

            # Attach plain text content FIRST
            if message.text_content:
                text_part = MIMEText(message.text_content, "plain")
                msg.attach(text_part)

            # Attach HTML content LAST
            html_part = MIMEText(message.html_content, "html")
            msg.attach(html_part)

            # Attach attachments if provided
            if message.attachments:
                for attachment in message.attachments:
                    from email import encoders
                    from email.mime.base import MIMEBase

                    part = MIMEBase(attachment.content_type.split("/")[0], attachment.content_type.split("/")[1])
                    part.set_payload(attachment.content)
                    encoders.encode_base64(part)
                    safe_name = _sanitize_header(attachment.filename)
                    part.add_header("Content-Disposition", f"attachment; filename={safe_name}")
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


class InMemoryEmailProvider(EmailProvider):
    """In-memory email provider fake for hermetic testing and verification."""

    def __init__(self) -> None:
        """Initialize in-memory email store."""
        self.sent_messages: list[EmailMessage] = []
        self.should_fail: bool = False
        self.failure_exception: Exception | None = None

    async def send_email(self, message: EmailMessage) -> bool:
        """Record message in memory or simulate failure."""
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        self.sent_messages.append(message)
        return True

    async def send_bulk_emails(self, messages: list[EmailMessage]) -> dict:
        """Record multiple messages in memory."""
        success = 0
        failed = 0
        for message in messages:
            if await self.send_email(message):
                success += 1
            else:
                failed += 1
        return {"success": success, "failed": failed}

    def clear(self) -> None:
        """Clear recorded messages and reset failure simulation flags."""
        self.sent_messages.clear()
        self.should_fail = False
        self.failure_exception = None

    @property
    def count(self) -> int:
        """Number of messages sent."""
        return len(self.sent_messages)

    @property
    def last_message(self) -> EmailMessage | None:
        """Most recently sent message."""
        return self.sent_messages[-1] if self.sent_messages else None

    def find_by_recipient(self, to_email: str) -> list[EmailMessage]:
        """Find sent messages by recipient email."""
        return [m for m in self.sent_messages if m.to_email == to_email]


_active_email_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """Get the active EmailProvider seam.

    Returns the explicitly configured provider if set.
    Otherwise resolves the default SMTPProvider from configuration.
    """
    global _active_email_provider
    if _active_email_provider is not None:
        return _active_email_provider

    from src.app.core.config import settings

    return SMTPProvider(
        host=settings.email.SMTP_HOST,
        port=settings.email.SMTP_PORT,
        username=settings.email.SMTP_USER,
        password=settings.email.SMTP_PASSWORD,
        use_tls=settings.email.SMTP_USE_TLS,
        from_email=settings.email.EMAIL_FROM_ADDRESS,
        from_name=settings.email.EMAIL_FROM_NAME,
    )


def set_email_provider(provider: EmailProvider | None) -> None:
    """Set the active EmailProvider seam (e.g. InMemoryEmailProvider for tests)."""
    global _active_email_provider
    _active_email_provider = provider


def reset_email_provider() -> None:
    """Reset the active EmailProvider seam to default."""
    global _active_email_provider
    _active_email_provider = None
