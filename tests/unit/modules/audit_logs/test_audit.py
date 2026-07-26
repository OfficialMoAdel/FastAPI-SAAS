from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.email_service import EmailService
from src.app.modules.notifications.models import NotificationStatus


@pytest.fixture
def email_service():
    """Create a fresh EmailService for each test."""
    service = EmailService()
    # Mock the database session maker
    service._session_maker = MagicMock()
    mock_session = AsyncMock()
    service._session_maker.return_value.__aenter__.return_value = mock_session
    return service, mock_session


async def test_get_provider(email_service):
    service, _ = email_service
    assert service._provider is None

    with patch("src.app.modules.notifications.email_service.SMTPProvider") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        provider = service._get_provider()

        assert provider is mock_instance
        assert service._provider is mock_instance


@patch("src.app.modules.notifications.email_service.Notification")
async def test_send_email_success(mock_notification_class, email_service):
    service, mock_session = email_service

    # Mock Notification instance
    mock_notification_instance = MagicMock()
    mock_notification_class.return_value = mock_notification_instance

    # Mock provider
    mock_provider = AsyncMock()
    mock_provider.send_email.return_value = True
    service._provider = mock_provider

    tenant_id = uuid4()

    result = await service.send_email(
        tenant_id=tenant_id,
        user_id=2,
        to_email="test@example.com",
        subject="Test Subject",
        html_content="<p>Test</p>",
        text_content="Test",
        template_name="test_template",
    )

    assert result is True

    # Verify email sending
    mock_provider.send_email.assert_called_once()
    message = mock_provider.send_email.call_args[0][0]
    assert message.to_email == "test@example.com"
    assert message.subject == "Test Subject"
    assert message.html_content == "<p>Test</p>"

    # Verify database logging
    mock_session.add.assert_called_once_with(mock_notification_instance)
    mock_session.commit.assert_called_once()

    # Verify Notification instantiation
    mock_notification_class.assert_called_once()
    kwargs = mock_notification_class.call_args[1]
    assert kwargs["to_email"] == "test@example.com"
    assert kwargs["subject"] == "Test Subject"
    assert kwargs["status"] == NotificationStatus.SENT
    assert kwargs["template_name"] == "test_template"
    assert mock_notification_instance.sent_at is not None


@patch("src.app.modules.notifications.email_service.Notification")
async def test_send_email_failure_from_provider(mock_notification_class, email_service):
    service, mock_session = email_service

    # Mock Notification instance
    mock_notification_instance = MagicMock()
    mock_notification_instance.error_message = None
    mock_notification_class.return_value = mock_notification_instance

    mock_provider = AsyncMock()
    mock_provider.send_email.return_value = False
    service._provider = mock_provider

    tenant_id = uuid4()

    result = await service.send_email(
        tenant_id=tenant_id, user_id=2, to_email="test@example.com", subject="Test Subject", html_content="<p>Test</p>"
    )

    assert result is False

    # Verify database logging
    mock_session.add.assert_called_once_with(mock_notification_instance)
    kwargs = mock_notification_class.call_args[1]
    assert kwargs["status"] == NotificationStatus.FAILED
    assert mock_notification_instance.error_message is None


@patch("src.app.modules.notifications.email_service.Notification")
async def test_send_email_exception(mock_notification_class, email_service):
    service, mock_session = email_service

    # Mock Notification instance
    mock_notification_instance = MagicMock()
    mock_notification_class.return_value = mock_notification_instance

    mock_provider = AsyncMock()
    mock_provider.send_email.side_effect = Exception("SMTP Error")
    service._provider = mock_provider

    tenant_id = uuid4()

    result = await service.send_email(
        tenant_id=tenant_id, user_id=2, to_email="test@example.com", subject="Test Subject", html_content="<p>Test</p>"
    )

    assert result is False

    # Verify database logging captured the error
    mock_session.add.assert_called_once_with(mock_notification_instance)
    kwargs = mock_notification_class.call_args[1]
    assert kwargs["status"] == NotificationStatus.FAILED
    assert mock_notification_instance.error_message == "SMTP Error"


@patch("src.app.modules.notifications.email_service.Notification")
async def test_send_bulk_emails(mock_notification_class, email_service):
    service, mock_session = email_service

    # Mock Notification instance
    mock_notification_instance = MagicMock()
    mock_notification_class.return_value = mock_notification_instance

    mock_provider = AsyncMock()
    mock_provider.send_bulk_emails.return_value = {"success": 2, "failed": 0}
    service._provider = mock_provider

    tenant_id = uuid4()
    messages = [
        {
            "tenant_id": tenant_id,
            "user_id": 2,
            "to_email": "test1@example.com",
            "subject": "Test 1",
            "html_content": "<p>1</p>",
        },
        {
            "tenant_id": tenant_id,
            "user_id": 3,
            "to_email": "test2@example.com",
            "subject": "Test 2",
            "html_content": "<p>2</p>",
            "template_name": "survey",
        },
    ]

    results = await service.send_bulk_emails(messages)

    assert "success" in results

    mock_provider.send_bulk_emails.assert_called_once()
    assert len(mock_provider.send_bulk_emails.call_args[0][0]) == 2

    assert mock_session.add.call_count == 2
    assert mock_session.commit.call_count == 2
    assert mock_notification_class.call_count == 2
