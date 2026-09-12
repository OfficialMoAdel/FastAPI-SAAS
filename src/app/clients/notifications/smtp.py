"""Email provider interface and SMTP / InMemory integration clients."""

import asyncio
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from aiosmtplib import SMTP


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


async def _close_quietly(conn: "SMTP") -> None:
    """Close an SMTP connection, swallowing shutdown errors."""
    try:
        if conn.is_connected:
            await conn.quit()
    except Exception:
        pass


class _SMTPConnectionPool:
    """Bounded pool of reusable authenticated SMTP connections.

    P2-D: ``aiosmtplib.send()`` performs a fresh TCP connect + STARTTLS +
    AUTH handshake for every email. The pool keeps up to ``max_idle`` live
    connections so consecutive sends (bulk fan-out, worker bursts) reuse one
    connection. Each connection is checked out for a single send
    (acquire → send → release); a failed send discards the connection
    because its session state is unknown.

    Loop-safety: aiosmtplib transports bind to the event loop that created
    them. When the running loop differs from the pool's loop (worker
    restart, pytest function-scoped loops), all idle connections are closed.
    """

    def __init__(self, max_idle: int = 4) -> None:
        self._max_idle = max_idle
        self._idle: deque[SMTP] = deque()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def acquire(self, provider: "SMTPProvider") -> "SMTP":
        """Return a connected (and STARTTLS-secured) SMTP connection."""
        from aiosmtplib import SMTP

        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            stale = list(self._idle)
            self._idle.clear()
            for conn in stale:
                await _close_quietly(conn)
            self._loop = loop

        while self._idle:
            conn = self._idle.popleft()
            if conn.is_connected:
                return conn
            await _close_quietly(conn)

        conn = SMTP(
            hostname=provider.host,
            port=provider.port,
            username=provider.username or None,
            password=provider.password.get_secret_value() if provider.password else None,
        )
        await conn.connect()
        if provider.use_tls:
            await conn.starttls()
        return conn

    async def release(self, conn: "SMTP") -> None:
        """Return a healthy connection to the pool, or close it."""
        if not conn.is_connected or len(self._idle) >= self._max_idle:
            await _close_quietly(conn)
            return
        self._idle.append(conn)

    async def discard(self, conn: "SMTP") -> None:
        """Close a connection without returning it to the pool."""
        await _close_quietly(conn)


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
        self._pool = _SMTPConnectionPool()

    async def send_email(self, message: EmailMessage) -> bool:
        """Send email via SMTP with circuit breaker protection."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr

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
                    # Pass filename as a header param so the stdlib quotes /
                    # RFC 2231-encodes special characters instead of splicing raw.
                    part.add_header("Content-Disposition", "attachment", filename=safe_name)
                    msg.attach(part)

            # P2-D: send over a pooled connection — reuses the TCP+STARTTLS+AUTH
            # handshake across sends instead of re-handshaking per email.
            # A failed send discards the connection (unknown session state);
            # success returns it to the idle pool.
            conn = await self._pool.acquire(self)
            try:
                await conn.send_message(msg)
            except Exception:
                await self._pool.discard(conn)
                raise
            await self._pool.release(conn)

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
# P2-D: cached default provider so its connection pool survives across calls;
# a fresh SMTPProvider per call would re-handshake every email and defeat pooling.
_default_smtp_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """Get the active EmailProvider seam.

    Returns the explicitly configured provider if set.
    Otherwise resolves the default SMTPProvider from configuration.
    """
    global _active_email_provider, _default_smtp_provider
    if _active_email_provider is not None:
        return _active_email_provider

    if _default_smtp_provider is None:
        from src.app.core.config import settings

        _default_smtp_provider = SMTPProvider(
            host=settings.email.SMTP_HOST,
            port=settings.email.SMTP_PORT,
            username=settings.email.SMTP_USER,
            password=settings.email.SMTP_PASSWORD,
            use_tls=settings.email.SMTP_USE_TLS,
            from_email=settings.email.EMAIL_FROM_ADDRESS,
            from_name=settings.email.EMAIL_FROM_NAME,
        )
    return _default_smtp_provider


def set_email_provider(provider: EmailProvider | None) -> None:
    """Set the active EmailProvider seam (e.g. InMemoryEmailProvider for tests)."""
    global _active_email_provider
    _active_email_provider = provider


def reset_email_provider() -> None:
    """Reset the EmailProvider seam to default.

    P1 FIX (code-review): also drops the cached default SMTP provider (and
    its pooled connections) so credential rotation takes effect without a
    process restart. Previously only _active was cleared and _default lived
    forever with stale creds/pool.
    """
    global _active_email_provider, _default_smtp_provider
    _active_email_provider = None
    default = _default_smtp_provider
    _default_smtp_provider = None
    if default is not None:
        try:
            pool = getattr(default, "_pool", None)
            if pool is not None and hasattr(pool, "close_all"):
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    loop.create_task(pool.close_all())  # type: ignore[attr-defined]
        except Exception:
            pass
