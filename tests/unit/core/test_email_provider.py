import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from src.app.clients.notifications import EmailAttachment, EmailMessage, SMTPProvider


@pytest.fixture
def smtp_provider():
    return SMTPProvider(
        host="smtp.example.com",
        port=587,
        username="user",
        password=SecretStr("pass"),
        use_tls=True,
        from_email="noreply@example.com",
        from_name="Test App",
    )


@pytest.fixture
def email_message():
    return EmailMessage(
        to_email="test@example.com",
        subject="Test Subject",
        html_content="<p>Hello World</p>",
        text_content="Hello World",
        reply_to="support@example.com",
        attachments=[EmailAttachment(filename="test.txt", content=b"hello", content_type="text/plain")],
    )


@pytest.fixture
def mock_smtp_cls():
    """Patch the SMTP class the pool constructs (P2-D pooled send path)."""
    with patch("aiosmtplib.SMTP") as cls:
        instance = cls.return_value
        instance.is_connected = True
        instance.send_message = AsyncMock()
        instance.connect = AsyncMock()
        instance.starttls = AsyncMock()
        instance.quit = AsyncMock()
        yield cls


@pytest.mark.asyncio
async def test_send_email_success(smtp_provider, email_message, mock_smtp_cls):
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = True
        mock_circuit.record_success = AsyncMock()

        result = await smtp_provider.send_email(email_message)

        assert result is True
        instance = mock_smtp_cls.return_value
        # P2-D: pooled connection handshake happens once per connection
        instance.connect.assert_awaited_once()
        instance.starttls.assert_awaited_once()
        instance.send_message.assert_awaited_once()
        # Healthy connection returned to the pool, not closed
        instance.quit.assert_not_awaited()
        mock_circuit.record_success.assert_awaited_once()

        # Verify message construction
        msg = instance.send_message.call_args[0][0]
        assert msg["To"] == "test@example.com"
        assert msg["Subject"] == "Test Subject"
        assert msg["From"] == "Test App <noreply@example.com>"
        assert msg["Reply-To"] == "support@example.com"


@pytest.mark.asyncio
async def test_send_email_pools_connection(smtp_provider, email_message, mock_smtp_cls):
    """P2-D: consecutive sends reuse one connection (one handshake)."""
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = True
        mock_circuit.record_success = AsyncMock()

        assert await smtp_provider.send_email(email_message) is True
        assert await smtp_provider.send_email(email_message) is True

        instance = mock_smtp_cls.return_value
        assert mock_smtp_cls.call_count == 1
        instance.connect.assert_awaited_once()
        assert instance.send_message.await_count == 2


@pytest.mark.asyncio
async def test_send_email_failure_discards_connection(smtp_provider, email_message, mock_smtp_cls):
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = True
        mock_circuit.record_failure = AsyncMock()
        mock_smtp_cls.return_value.send_message.side_effect = Exception("SMTP Error")

        result = await smtp_provider.send_email(email_message)

        assert result is False
        instance = mock_smtp_cls.return_value
        # Failed send must NOT return the connection to the pool
        instance.quit.assert_awaited_once()
        mock_circuit.record_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_email_pool_drops_stale_loop_connections(smtp_provider, email_message, mock_smtp_cls):
    """P2-D: connections are loop-bound — a new loop must not reuse them."""
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = True
        mock_circuit.record_success = AsyncMock()
        await smtp_provider.send_email(email_message)
        instance = mock_smtp_cls.return_value
        instance.quit.reset_mock()

        # Simulate an event-loop change (worker restart / new pytest loop)
        stale_loop = asyncio.new_event_loop()
        stale_loop.close()
        smtp_provider._pool._loop = stale_loop

        await smtp_provider.send_email(email_message)

        # Stale idle connection closed, fresh handshake for the new loop
        instance.quit.assert_awaited_once()
        assert mock_smtp_cls.call_count == 2


@pytest.mark.asyncio
async def test_send_email_circuit_open(smtp_provider, email_message):
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = False

        result = await smtp_provider.send_email(email_message)
        assert result is False


@pytest.mark.asyncio
async def test_send_bulk_emails(smtp_provider):
    with patch.object(smtp_provider, "send_email", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = [True, False, True]

        messages = [
            EmailMessage(to_email="1@test.com", subject="T", html_content="H"),
            EmailMessage(to_email="2@test.com", subject="T", html_content="H"),
            EmailMessage(to_email="3@test.com", subject="T", html_content="H"),
        ]

        result = await smtp_provider.send_bulk_emails(messages)

        assert result == {"success": 2, "failed": 1}
        assert mock_send.call_count == 3


def test_get_email_provider_caches_default_instance():
    """P2-D: default provider is a cached singleton so its pool persists."""
    from src.app.clients.notifications import smtp as smtp_module

    smtp_module._default_smtp_provider = None
    try:
        first = smtp_module.get_email_provider()
        second = smtp_module.get_email_provider()
        assert first is second
        assert isinstance(first, SMTPProvider)
    finally:
        smtp_module._default_smtp_provider = None
