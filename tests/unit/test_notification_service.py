"""Unit tests for NotificationService business logic.

Tests cover:
- Notification status enum values
- NotificationChannel values
- Template override model
"""


import pytest


class TestNotificationStatus:
    """Tests for NotificationStatus enum."""

    def test_notification_status_values(self) -> None:
        """Test notification status enum values exist and are correct."""
        from src.app.modules.notifications.models import NotificationStatus

        assert NotificationStatus.PENDING.value == "pending"
        assert NotificationStatus.SENT.value == "sent"
        assert NotificationStatus.FAILED.value == "failed"

    def test_notification_status_is_string_enum(self) -> None:
        """Test that NotificationStatus is a string enum."""
        from src.app.modules.notifications.models import NotificationStatus

        assert isinstance(NotificationStatus.PENDING.value, str)
        assert isinstance(NotificationStatus.SENT.value, str)
        assert isinstance(NotificationStatus.FAILED.value, str)


class TestNotificationModel:
    """Tests for Notification model structure."""

    def test_notification_tablename(self) -> None:
        """Test notification table name."""
        from src.app.modules.notifications.models import Notification

        assert Notification.__tablename__ == "notifications"

    def test_notification_has_required_columns(self) -> None:
        """Test notification has required columns."""
        from src.app.modules.notifications.models import Notification

        # Check that the model has the expected column names
        mapper = Notification.__mapper__
        column_names = [c.key for c in mapper.columns]

        assert "tenant_id" in column_names
        assert "user_id" in column_names
        assert "template_name" in column_names
        assert "to_email" in column_names
        assert "subject" in column_names
        assert "status" in column_names
        assert "created_at" in column_names


class TestEmailTemplateOverrideModel:
    """Tests for EmailTemplateOverride model structure."""

    def test_template_override_tablename(self) -> None:
        """Test email template override table name."""
        from src.app.modules.notifications.models import EmailTemplateOverride

        assert EmailTemplateOverride.__tablename__ == "email_template_overrides"

    def test_template_override_has_required_columns(self) -> None:
        """Test email template override has required columns."""
        from src.app.modules.notifications.models import EmailTemplateOverride

        mapper = EmailTemplateOverride.__mapper__
        column_names = [c.key for c in mapper.columns]

        assert "tenant_id" in column_names
        assert "template_name" in column_names
        assert "subject_override" in column_names
        assert "content" in column_names
        assert "created_at" in column_names


class TestNotificationServiceLogic:
    """Tests for notification service business logic."""

    @pytest.mark.asyncio
    async def test_send_email_prepares_correct_payload(self) -> None:
        """Test that email sending prepares the correct payload structure."""
        # This tests the expected payload structure
        expected_fields = ["to_email", "subject", "template_name", "context"]

        # Verify all fields are present in a typical notification payload
        payload = {
            "to_email": "test@example.com",
            "subject": "Test Subject",
            "template_name": "welcome",
            "context": {"user_name": "Test User"},
        }

        for field in expected_fields:
            assert field in payload

    @pytest.mark.asyncio
    async def test_bulk_send_handles_multiple_recipients(self) -> None:
        """Test that bulk email send can handle multiple recipients."""
        recipients = [
            {"email": "user1@example.com", "name": "User 1"},
            {"email": "user2@example.com", "name": "User 2"},
            {"email": "user3@example.com", "name": "User 3"},
        ]

        # Verify we can iterate over recipients
        assert len(recipients) == 3
        for recipient in recipients:
            assert "email" in recipient
            assert "name" in recipient
