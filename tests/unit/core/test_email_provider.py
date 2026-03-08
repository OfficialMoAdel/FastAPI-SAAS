from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from src.app.email_provider import EmailAttachment, EmailMessage, SMTPProvider


@pytest.fixture
def smtp_provider():
    return SMTPProvider(
        host="smtp.example.com",
        port=587,
        username="user",
        password=SecretStr("pass"),
        use_tls=True,
        from_email="noreply@example.com",
        from_name="Test App"
    )

@pytest.fixture
def email_message():
    return EmailMessage(
        to_email="test@example.com",
        subject="Test Subject",
        html_content="<p>Hello World</p>",
        text_content="Hello World",
        reply_to="support@example.com",
        attachments=[
            EmailAttachment(
                filename="test.txt",
                content=b"hello",
                content_type="text/plain"
            )
        ]
    )

@pytest.mark.asyncio
async def test_send_email_success(smtp_provider, email_message):
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send, \
         patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:

        mock_circuit.is_closed = True
        mock_circuit.record_success = AsyncMock()

        result = await smtp_provider.send_email(email_message)

        assert result is True
        mock_send.assert_awaited_once()
        mock_circuit.record_success.assert_awaited_once()

        # Verify message construction
        msg = mock_send.call_args[0][0]
        assert msg["To"] == "test@example.com"
        assert msg["Subject"] == "Test Subject"
        assert msg["From"] == "Test App <noreply@example.com>"
        assert msg["Reply-To"] == "support@example.com"

@pytest.mark.asyncio
async def test_send_email_circuit_open(smtp_provider, email_message):
    with patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:
        mock_circuit.is_closed = False

        result = await smtp_provider.send_email(email_message)
        assert result is False

@pytest.mark.asyncio
async def test_send_email_failure(smtp_provider, email_message):
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send, \
         patch("src.app.core.utils.circuit_breaker.email_circuit") as mock_circuit:

        mock_circuit.is_closed = True
        mock_circuit.record_failure = AsyncMock()
        mock_send.side_effect = Exception("SMTP Error")

        result = await smtp_provider.send_email(email_message)

        assert result is False
        mock_circuit.record_failure.assert_awaited_once()

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
