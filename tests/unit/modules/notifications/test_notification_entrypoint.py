"""Unit tests for Candidate 8: Single Notification Entrypoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import (
    NotificationService,
    request_notification,
)


@pytest.mark.asyncio
async def test_send_email_enqueues_directly_to_arq():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    service = NotificationService(mock_db)
    service.render_template = AsyncMock(return_value=("Subject", "<p>Body</p>"))

    mock_arq = AsyncMock()
    mock_arq.enqueue_job = AsyncMock()

    t_id = uuid4()
    with patch("src.app.core.utils.queue.pool", mock_arq):
        notif = await service.send_email(
            tenant_id=t_id,
            to_email="test@example.com",
            template_name="welcome",
            context={"name": "Alice"},
        )

    assert notif is not None
    assert notif.status == NotificationStatus.PENDING
    mock_arq.enqueue_job.assert_awaited_once()
    args = mock_arq.enqueue_job.call_args[0]
    assert args[0] == "send_email_task"
    assert args[2] == "test@example.com"
    assert args[3] == "Subject"


@pytest.mark.asyncio
async def test_send_email_arq_unavailable_fails_gracefully():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    service = NotificationService(mock_db)
    service.render_template = AsyncMock(return_value=("Subject", "<p>Body</p>"))

    t_id = uuid4()
    with patch("src.app.core.utils.queue.pool", None):
        notif = await service.send_email(
            tenant_id=t_id,
            to_email="test@example.com",
            template_name="welcome",
            context={"name": "Alice"},
        )

    assert notif is not None
    assert notif.status == NotificationStatus.FAILED
    assert notif.error_message == "ARQ queue not available"


@pytest.mark.asyncio
async def test_request_notification_publishes_to_outbox():
    with patch("src.app.modules.notifications.service.event_bus.publish", new_callable=AsyncMock) as mock_pub:
        await request_notification(
            to_email="bob@example.com",
            template_name="magic_link",
            context={"link": "https://example.com/login"},
        )
    mock_pub.assert_awaited_once()
    event_name, payload = mock_pub.call_args[0][:2]
    assert event_name == "notification.request"
    assert payload["to_email"] == "bob@example.com"
    assert payload["template_name"] == "magic_link"
