"""Unit tests for the notification outbox seam and delivery provider ports & adapters."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.clients.notifications import (
    EmailMessage,
    InMemoryEmailProvider,
    SMTPProvider,
    get_email_provider,
    reset_email_provider,
    set_email_provider,
)
from src.app.core.events.notifications import request_notification
from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import (
    handle_notification_request_event,
    send_email_task,
)


class TestInMemoryEmailProvider:
    """Tests for InMemoryEmailProvider test fake."""

    @pytest.mark.asyncio
    async def test_in_memory_provider_records_deliveries(self) -> None:
        """Provider records messages in memory and provides inspectable attributes."""
        provider = InMemoryEmailProvider()
        msg1 = EmailMessage(to_email="alice@example.com", subject="Hello", html_content="<p>Hi Alice</p>")
        msg2 = EmailMessage(to_email="bob@example.com", subject="Welcome", html_content="<p>Hi Bob</p>")

        assert await provider.send_email(msg1) is True
        assert await provider.send_email(msg2) is True

        assert provider.count == 2
        assert provider.last_message == msg2
        assert len(provider.find_by_recipient("alice@example.com")) == 1
        assert provider.find_by_recipient("alice@example.com")[0].subject == "Hello"

        provider.clear()
        assert provider.count == 0
        assert provider.last_message is None

    @pytest.mark.asyncio
    async def test_in_memory_provider_bulk_send(self) -> None:
        """Provider handles bulk email sending correctly."""
        provider = InMemoryEmailProvider()
        messages = [
            EmailMessage(to_email="user1@example.com", subject="Subj1", html_content="<p>1</p>"),
            EmailMessage(to_email="user2@example.com", subject="Subj2", html_content="<p>2</p>"),
        ]

        result = await provider.send_bulk_emails(messages)
        assert result == {"success": 2, "failed": 0}
        assert provider.count == 2

    @pytest.mark.asyncio
    async def test_in_memory_provider_simulated_failure(self) -> None:
        """Provider returns False when configured to fail."""
        provider = InMemoryEmailProvider()
        provider.should_fail = True

        msg = EmailMessage(to_email="fail@example.com", subject="Fail", html_content="<p>Fail</p>")
        assert await provider.send_email(msg) is False
        assert provider.count == 0

    @pytest.mark.asyncio
    async def test_in_memory_provider_simulated_exception(self) -> None:
        """Provider raises failure_exception when configured."""
        provider = InMemoryEmailProvider()
        provider.should_fail = True
        provider.failure_exception = ConnectionRefusedError("SMTP server down")

        msg = EmailMessage(to_email="fail@example.com", subject="Fail", html_content="<p>Fail</p>")
        with pytest.raises(ConnectionRefusedError, match="SMTP server down"):
            await provider.send_email(msg)


class TestEmailProviderSeam:
    """Tests for the get/set/reset EmailProvider seam."""

    def teardown_method(self) -> None:
        reset_email_provider()

    def test_default_provider_resolution(self) -> None:
        """Default provider resolves to SMTPProvider from configuration."""
        reset_email_provider()
        provider = get_email_provider()
        assert isinstance(provider, SMTPProvider)

    def test_override_and_reset_provider(self) -> None:
        """Setting custom provider overrides resolution until reset."""
        fake = InMemoryEmailProvider()
        set_email_provider(fake)
        assert get_email_provider() is fake

        reset_email_provider()
        assert isinstance(get_email_provider(), SMTPProvider)


class TestRequestNotification:
    """Tests for standardized request_notification event dispatch."""

    @pytest.mark.asyncio
    @patch("src.app.core.events.notifications.event_bus.publish", new_callable=AsyncMock)
    async def test_request_notification_publishes_standard_event(self, mock_publish) -> None:
        """request_notification formats payload and publishes notification.request."""
        tenant_id = uuid4()
        await request_notification(
            to_email="test@example.com",
            template_name="welcome",
            context={"name": "Alice"},
            tenant_id=tenant_id,
            user_id=42,
        )

        mock_publish.assert_awaited_once()
        args, kwargs = mock_publish.call_args
        assert args[0] == "notification.request"
        payload = args[1]
        assert payload["to_email"] == "test@example.com"
        assert payload["template_name"] == "welcome"
        assert payload["context"] == {"name": "Alice"}
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["user_id"] == 42


class TestHandleNotificationRequestEventOutbox:
    """Tests for handle_notification_request_event transactional outbox handling."""

    @pytest.mark.asyncio
    async def test_missing_data_early_exit(self) -> None:
        """Missing to_email or template_name logs warning and returns without DB access."""
        with patch("src.app.core.db.database.local_session") as mock_session:
            await handle_notification_request_event({})
            mock_session.assert_not_called()

            await handle_notification_request_event({"to_email": "a@b.com"})
            mock_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_opt_out_skips_outbox_row(self) -> None:
        """When recipient has disabled notification type, outbox row is not created."""
        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db

        with (
            patch("src.app.core.db.database.local_session", return_value=mock_session_ctx),
            patch(
                "src.app.modules.notifications.service.NotificationService.should_send_notification",
                new_callable=AsyncMock,
            ) as mock_pref,
        ):
            mock_pref.return_value = False

            await handle_notification_request_event({
                "to_email": "user@example.com",
                "template_name": "marketing",
                "user_id": 99,
            })

            mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.app.core.utils.queue.pool")
    @patch("src.app.modules.notifications.service.rate_limiter.check_rate_limit", new_callable=AsyncMock)
    async def test_outbox_row_created_and_task_enqueued(self, mock_rate, mock_arq) -> None:
        """Creates PENDING Notification record and directly enqueues send_email_task to ARQ."""
        mock_rate.return_value = (False, {})
        mock_arq.enqueue_job = AsyncMock()

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db

        t_id = uuid4()
        data = {
            "to_email": "alice@example.com",
            "template_name": "welcome",
            "context": {"username": "alice"},
            "tenant_id": str(t_id),
            "user_id": 1,
        }

        with (
            patch("src.app.core.db.database.local_session", return_value=mock_session_ctx),
            patch(
                "src.app.modules.notifications.service.NotificationService.render_template",
                new_callable=AsyncMock,
            ) as mock_render,
            patch(
                "src.app.modules.notifications.service.NotificationService.should_send_notification",
                new_callable=AsyncMock,
            ) as mock_pref,
        ):
            mock_pref.return_value = True
            mock_render.return_value = ("Welcome to SaaS", "<p>Welcome alice</p>")

            await handle_notification_request_event(data)

            # Notification record added and committed
            mock_db.add.assert_called_once()
            added_notif = mock_db.add.call_args[0][0]
            assert added_notif.to_email == "alice@example.com"
            assert added_notif.subject == "Welcome to SaaS"
            assert added_notif.status == NotificationStatus.PENDING

            # ARQ task directly enqueued with notification id and rendered body
            mock_arq.enqueue_job.assert_awaited_once()
            job_args = mock_arq.enqueue_job.call_args[0]
            assert job_args[0] == "send_email_task"
            assert job_args[2] == "alice@example.com"
            assert job_args[3] == "Welcome to SaaS"
            assert job_args[4] == "<p>Welcome alice</p>"
            assert job_args[5] == str(t_id)

    @pytest.mark.asyncio
    @patch("src.app.core.utils.queue.pool", None)
    @patch("src.app.modules.notifications.service.rate_limiter.check_rate_limit", new_callable=AsyncMock)
    async def test_outbox_marked_failed_when_queue_unavailable(self, mock_rate) -> None:
        """When ARQ is unavailable, notification is recorded as FAILED rather than lost."""
        mock_rate.return_value = (False, {})

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db

        data = {
            "to_email": "bob@example.com",
            "template_name": "password_reset",
            "context": {},
        }

        with (
            patch("src.app.core.db.database.local_session", return_value=mock_session_ctx),
            patch(
                "src.app.modules.notifications.service.NotificationService.render_template",
                new_callable=AsyncMock,
            ) as mock_render,
        ):
            mock_render.return_value = ("Reset Password", "<p>Reset</p>")

            await handle_notification_request_event(data)

            mock_db.add.assert_called_once()
            added_notif = mock_db.add.call_args[0][0]
            assert added_notif.status == NotificationStatus.FAILED
            assert added_notif.error_message == "ARQ queue not available"


class TestHermeticDeliveryWithInMemoryProvider:
    """Tests executing send_email_task hermetically with InMemoryEmailProvider."""

    def teardown_method(self) -> None:
        reset_email_provider()

    @pytest.mark.asyncio
    @patch("src.app.core.db.database.async_session_maker")
    async def test_send_email_task_hermetic_success(self, mock_session_maker) -> None:
        """send_email_task uses InMemoryEmailProvider and marks notification SENT without network."""
        fake_provider = InMemoryEmailProvider()
        set_email_provider(fake_provider)

        mock_db = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_db

        notif_id = uuid4()

        with patch("src.app.modules.notifications.service.NotificationService") as mock_srv_cls:
            mock_srv = AsyncMock()
            mock_srv_cls.return_value = mock_srv

            await send_email_task(
                ctx={},
                notification_id=str(notif_id),
                to_email="hermetic@example.com",
                subject="Test Hermetic",
                html_content="<p>Test</p>",
            )

            # In-memory delivery verified
            assert fake_provider.count == 1
            assert fake_provider.last_message.to_email == "hermetic@example.com"
            assert fake_provider.last_message.subject == "Test Hermetic"

            # DB marked sent
            mock_srv.mark_sent.assert_awaited_once_with(notif_id)

    @pytest.mark.asyncio
    @patch("src.app.core.db.database.async_session_maker")
    async def test_send_email_task_hermetic_failure(self, mock_session_maker) -> None:
        """send_email_task marks notification FAILED when delivery provider raises."""
        fake_provider = InMemoryEmailProvider()
        fake_provider.should_fail = True
        fake_provider.failure_exception = RuntimeError("SMTP connection timeout")
        set_email_provider(fake_provider)

        mock_db = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_db

        notif_id = uuid4()

        with patch("src.app.modules.notifications.service.NotificationService") as mock_srv_cls:
            mock_srv = AsyncMock()
            mock_srv_cls.return_value = mock_srv

            with pytest.raises(RuntimeError, match="SMTP connection timeout"):
                await send_email_task(
                    ctx={},
                    notification_id=str(notif_id),
                    to_email="fail@example.com",
                    subject="Fail",
                    html_content="<p>Fail</p>",
                )

            mock_srv.mark_failed.assert_awaited_once()


class TestMagicLinkTaskOutboxSeam:
    """Tests that send_magic_link_email_task dispatches via notification.request seam."""

    @pytest.mark.asyncio
    @patch("src.app.core.events.notifications.event_bus.publish", new_callable=AsyncMock)
    async def test_send_magic_link_email_task_publishes_notification_request(self, mock_publish) -> None:
        """send_magic_link_email_task publishes notification.request with no direct NotificationService."""
        from src.app.modules.auth.tasks import send_magic_link_email_task

        mock_ctx = MagicMock()
        result = await send_magic_link_email_task(
            ctx=mock_ctx,
            email="magic@example.com",
            magic_link_url="https://app.example.com/magic?token=xyz",
            user_id=123,
        )

        assert "magic@example.com" in result
        mock_publish.assert_awaited_once()
        args, kwargs = mock_publish.call_args
        assert args[0] == "notification.request"
        payload = args[1]
        assert payload["to_email"] == "magic@example.com"
        assert payload["template_name"] == "magic_link"
        assert payload["context"]["magic_link_url"] == "https://app.example.com/magic?token=xyz"
        assert payload["user_id"] == 123


class TestAuthCriticalExemption:
    """Tests for P1-1: auth-critical transactional emails bypass user preference gates."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("template_name", ["email_verification", "magic_link", "password_reset"])
    @patch("src.app.core.utils.queue.pool")
    @patch("src.app.modules.notifications.service.rate_limiter.check_rate_limit", new_callable=AsyncMock)
    async def test_p1_1_auth_critical_templates_bypass_preferences(self, mock_rate, mock_arq, template_name) -> None:
        """Auth-critical templates are never skipped by user preferences or quiet hours."""
        mock_rate.return_value = (False, {})
        mock_arq.enqueue_job = AsyncMock()

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db

        with (
            patch("src.app.core.db.database.local_session", return_value=mock_session_ctx),
            patch(
                "src.app.modules.notifications.service.NotificationService.render_template",
                new_callable=AsyncMock,
            ) as mock_render,
            patch(
                "src.app.modules.notifications.service.NotificationService.should_send_notification",
                new_callable=AsyncMock,
            ) as mock_pref,
        ):
            # Preferences evaluate to False (user disabled notifications or in quiet hours)
            mock_pref.return_value = False
            mock_render.return_value = ("Auth Action", "<p>Link</p>")

            await handle_notification_request_event({
                "to_email": "user@example.com",
                "template_name": template_name,
                "user_id": 42,
            })

            # should_send_notification must not even be checked for auth-critical templates
            mock_pref.assert_not_called()

            # Notification MUST be added to DB outbox and enqueued to ARQ
            mock_db.add.assert_called_once()
            mock_arq.enqueue_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_p2_4_malformed_tenant_uuid_rejected_early(self) -> None:
        """Malformed tenant UUID logs warning and exits without touching DB."""
        with patch("src.app.core.db.database.local_session") as mock_session:
            await handle_notification_request_event({
                "to_email": "user@example.com",
                "template_name": "welcome",
                "tenant_id": "not-a-valid-uuid",
            })
            mock_session.assert_not_called()

    def test_p3_6_in_memory_provider_clear_resets_flags(self) -> None:
        """InMemoryEmailProvider.clear() resets should_fail and failure_exception flags."""
        provider = InMemoryEmailProvider()
        provider.should_fail = True
        provider.failure_exception = RuntimeError("test error")

        provider.clear()

        assert provider.should_fail is False
        assert provider.failure_exception is None
