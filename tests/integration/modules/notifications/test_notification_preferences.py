"""Notification preferences tests - Unit and Integration tests combined.

This file contains:
- Unit tests: Model, schemas, validation
- Integration tests: NotificationService behavior with preferences
"""

from datetime import UTC, datetime, time
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import NotificationService
from src.app.modules.user_preferences.models import UserPreferences
from src.app.modules.user_preferences.schemas import UserPreferencesUpdate
from tests.conftest import MockUser

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
async def async_db(setup_db):
    """Async database session for testing."""
    from tests.conftest import get_testing_session_local

    async with get_testing_session_local()() as session:
        yield session


# ============================================================================
# UNIT TESTS - Model & Schemas
# ============================================================================


class TestUserPreferencesModel:
    """Test UserPreferences model with notification preferences."""

    @pytest.mark.asyncio
    async def test_notification_preferences_defaults(self, async_db: AsyncSession):
        """Test default values for notification preferences."""
        # Note: notification preference columns have init=False, so they're not passed to constructor
        # They will have database defaults
        prefs = UserPreferences(user_id=123, preferences={})
        async_db.add(prefs)
        await async_db.commit()
        await async_db.refresh(prefs)

        # Check defaults from database schema
        assert prefs.email_notifications_enabled is True  # default=True in migration
        assert prefs.push_notifications_enabled is True  # default=True in migration
        assert prefs.disabled_notification_types == []  # default=[] in migration
        assert prefs.quiet_hours_enabled is False  # default=False in migration
        assert prefs.quiet_hours_start is None
        assert prefs.quiet_hours_end is None

    @pytest.mark.asyncio
    async def test_notification_preferences_can_be_set(self, async_db: AsyncSession):
        """Test notification preferences can be explicitly set via database."""
        # Create with defaults
        prefs = UserPreferences(user_id=124, preferences={})
        async_db.add(prefs)
        await async_db.flush()  # Get the ID but don't commit yet

        # Update notification preferences directly on the instance
        # These will be saved when we commit
        prefs.email_notifications_enabled = False
        prefs.push_notifications_enabled = False
        prefs.disabled_notification_types = ["marketing", "tips"]
        prefs.quiet_hours_enabled = True
        prefs.quiet_hours_start = "22:00"
        prefs.quiet_hours_end = "07:00"

        await async_db.commit()
        await async_db.refresh(prefs)

        assert prefs.email_notifications_enabled is False
        assert prefs.push_notifications_enabled is False
        assert prefs.disabled_notification_types == ["marketing", "tips"]
        assert prefs.quiet_hours_enabled is True
        assert prefs.quiet_hours_start == "22:00"
        assert prefs.quiet_hours_end == "07:00"


class TestNotificationPreferencesSchemas:
    """Test notification preferences schemas."""

    def test_user_preferences_update_with_notification_settings(self):
        """Test UserPreferencesUpdate with notification settings."""
        data = {
            "email_notifications_enabled": False,
            "push_notifications_enabled": True,
            "disabled_notification_types": ["marketing"],
            "quiet_hours_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        }
        prefs = UserPreferencesUpdate(**data)

        assert prefs.email_notifications_enabled is False
        assert prefs.push_notifications_enabled is True
        assert prefs.disabled_notification_types == ["marketing"]
        assert prefs.quiet_hours_enabled is True
        assert prefs.quiet_hours_start == "22:00"
        assert prefs.quiet_hours_end == "07:00"

    def test_user_preferences_update_notification_settings(self):
        """Test UserPreferencesUpdate with notification settings."""
        data = {
            "email_notifications_enabled": False,
            "disabled_notification_types": ["marketing", "tips"],
        }
        prefs = UserPreferencesUpdate(**data)

        assert prefs.email_notifications_enabled is False
        assert prefs.disabled_notification_types == ["marketing", "tips"]

    def test_quiet_hours_validation_same_times(self):
        """Test that quiet hours start and end cannot be equal."""
        data = {
            "quiet_hours_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "22:00",
        }

        with pytest.raises(ValueError, match="cannot be equal"):
            UserPreferencesUpdate(**data)

    def test_quiet_hours_validation_valid_range(self):
        """Test valid quiet hours range."""
        data = {
            "quiet_hours_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        }
        prefs = UserPreferencesUpdate(**data)

        assert prefs.quiet_hours_start == "22:00"
        assert prefs.quiet_hours_end == "07:00"

    def test_quiet_hours_format_validation(self):
        """Test quiet hours time format validation."""
        # Valid formats
        valid_times = ["00:00", "23:59", "01:00", "12:30"]
        for t in valid_times:
            data = {
                "quiet_hours_enabled": True,
                "quiet_hours_start": t,
                "quiet_hours_end": "23:00",
            }
            prefs = UserPreferencesUpdate(**data)
            assert prefs.quiet_hours_start == t

        # Invalid formats
        invalid_times = ["25:00", "24:00", "ab:cd", "1234", "1:00"]
        for t in invalid_times:
            data = {
                "quiet_hours_enabled": True,
                "quiet_hours_start": t,
                "quiet_hours_end": "23:00",
            }
            with pytest.raises(ValueError):
                UserPreferencesUpdate(**data)

    def test_disabled_notification_types_validation(self):
        """Test disabled_notification_types field."""
        data = {
            "disabled_notification_types": ["marketing", "tips", "news"],
        }
        prefs = UserPreferencesUpdate(**data)

        assert len(prefs.disabled_notification_types) == 3
        assert "marketing" in prefs.disabled_notification_types

    def test_disabled_notification_types_empty_list(self):
        """Test disabled_notification_types with empty list."""
        data = {
            "disabled_notification_types": [],
        }
        prefs = UserPreferencesUpdate(**data)

        assert prefs.disabled_notification_types == []


class TestQuietHoursLogic:
    """Test quiet hours logic and time calculations."""

    def test_same_day_quiet_hours(self):
        """Test quiet hours within same day (e.g., 22:00 - 23:59)."""
        start = time.fromisoformat("22:00")
        end = time.fromisoformat("23:59")

        # Test times within range
        test_time = time.fromisoformat("22:30")
        assert start <= test_time < end

        # Test times outside range
        test_time = time.fromisoformat("21:59")
        assert not (start <= test_time < end)

        test_time = time.fromisoformat("00:00")
        assert not (start <= test_time < end)

    def test_cross_day_quiet_hours(self):
        """Test quiet hours crossing midnight (e.g., 22:00 - 07:00)."""
        start = time.fromisoformat("22:00")
        end = time.fromisoformat("07:00")

        # Test times within range (evening)
        test_time = time.fromisoformat("22:30")
        assert test_time >= start or test_time < end

        # Test times within range (morning)
        test_time = time.fromisoformat("06:00")
        assert test_time >= start or test_time < end

        # Test times outside range
        test_time = time.fromisoformat("21:59")
        assert not (test_time >= start or test_time < end)

        test_time = time.fromisoformat("07:01")
        assert not (test_time >= start or test_time < end)

    def test_quiet_hours_boundary_conditions(self):
        """Test quiet hours at boundary conditions."""
        start = time.fromisoformat("22:00")
        end = time.fromisoformat("07:00")

        # Start time is included
        test_time = time.fromisoformat("22:00")
        assert test_time >= start or test_time < end

        # End time is excluded
        test_time = time.fromisoformat("07:00")
        assert not (test_time >= start or test_time < end)


class TestNotificationPreferencesIntegration:
    """Integration tests for notification preferences."""

    @pytest.mark.asyncio
    async def test_update_notification_preferences(self, async_db: AsyncSession):
        """Test updating notification preferences."""
        # Create initial preferences
        prefs = UserPreferences(user_id=125, preferences={})
        async_db.add(prefs)
        await async_db.commit()
        await async_db.refresh(prefs)

        # Verify defaults
        assert prefs.email_notifications_enabled is True
        assert prefs.disabled_notification_types == []

        # Update preferences
        prefs.email_notifications_enabled = False
        prefs.disabled_notification_types = ["marketing"]
        prefs.quiet_hours_enabled = True
        prefs.quiet_hours_start = "22:00"
        prefs.quiet_hours_end = "07:00"
        await async_db.commit()
        await async_db.refresh(prefs)

        assert prefs.email_notifications_enabled is False
        assert prefs.disabled_notification_types == ["marketing"]
        assert prefs.quiet_hours_enabled is True
        assert prefs.quiet_hours_start == "22:00"
        assert prefs.quiet_hours_end == "07:00"


# ============================================================================
# INTEGRATION TESTS - NotificationService Behavior
# ============================================================================


@pytest.mark.asyncio
async def test_notification_skipped_when_email_disabled(async_db: AsyncSession):
    """Test that notifications are skipped when email notifications are disabled."""
    user_id = 1001
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Create user preferences with email notifications disabled
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences directly
    prefs.email_notifications_enabled = False
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Try to send email
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="test_template",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was created but skipped by preferences
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert "Skipped" in notification.error_message


@pytest.mark.asyncio
async def test_notification_sent_when_email_enabled(async_db: AsyncSession):
    """Test that notifications are sent when email notifications are enabled."""
    user_id = 1002
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Create user preferences with email notifications enabled (default)
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.commit()
    await async_db.refresh(prefs)

    # Verify default
    assert prefs.email_notifications_enabled is True

    # Create notifications service
    service = NotificationService(async_db)

    # Send email
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="welcome",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was created and queued
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert notification.to_email == "test@example.com"


@pytest.mark.asyncio
async def test_notification_skipped_when_type_disabled(async_db: AsyncSession):
    """Test that notifications are skipped when the type is in disabled list."""
    user_id = 1003
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Create user preferences with marketing notifications disabled
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.disabled_notification_types = ["marketing", "tips"]
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Try to send marketing email
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="marketing",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was skipped
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert "Skipped" in notification.error_message


@pytest.mark.asyncio
async def test_notification_sent_when_type_not_disabled(async_db: AsyncSession):
    """Test that notifications are sent when the type is not in disabled list."""
    user_id = 1004
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Create user preferences with some types disabled but not the one we're sending
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.disabled_notification_types = ["marketing"]
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Send welcome email (not in disabled list)
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="welcome",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was queued
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING


@pytest.mark.asyncio
async def test_notification_skipped_during_quiet_hours(async_db: AsyncSession):
    """Test that notifications are skipped during quiet hours."""
    user_id = 1005

    # Create user preferences with quiet hours enabled
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.quiet_hours_enabled = True
    prefs.quiet_hours_start = "22:00"
    prefs.quiet_hours_end = "07:00"
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Simulate current time during quiet hours (23:00)
    test_time = datetime.now(UTC).replace(hour=23, minute=0, second=0, microsecond=0)

    notification = await service.should_send_notification(user_id, "welcome", test_time)

    # Verify notification should not be sent
    assert notification is False


@pytest.mark.asyncio
async def test_notification_sent_outside_quiet_hours(async_db: AsyncSession):
    """Test that notifications are sent outside quiet hours."""
    user_id = 1006

    # Create user preferences with quiet hours enabled
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.quiet_hours_enabled = True
    prefs.quiet_hours_start = "22:00"
    prefs.quiet_hours_end = "07:00"
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Simulate current time outside quiet hours (10:00)
    test_time = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)

    notification = await service.should_send_notification(user_id, "welcome", test_time)

    # Verify notification should be sent
    assert notification is True


@pytest.mark.asyncio
async def test_notification_sent_when_quiet_hours_disabled(async_db: AsyncSession):
    """Test that notifications are sent when quiet hours are disabled."""
    user_id = 1007

    # Create user preferences with quiet hours disabled
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.commit()

    # Verify default
    assert prefs.quiet_hours_enabled is False

    # Create notifications service
    service = NotificationService(async_db)

    # Simulate any time (should not matter)
    test_time = datetime.now(UTC).replace(hour=23, minute=0)

    notification = await service.should_send_notification(user_id, "welcome", test_time)

    # Verify notification should be sent
    assert notification is True


@pytest.mark.asyncio
async def test_default_preferences_allow_notifications(async_db: AsyncSession):
    """Test that default preferences allow notifications."""
    user_id = 1008
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Create user preferences with defaults
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.commit()
    await async_db.refresh(prefs)

    # Verify defaults
    assert prefs.email_notifications_enabled is True
    assert prefs.disabled_notification_types == []
    assert prefs.quiet_hours_enabled is False

    # Create notifications service
    service = NotificationService(async_db)

    # Send email
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="welcome",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was queued
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING


@pytest.mark.asyncio
async def test_cross_day_quiet_hours(async_db: AsyncSession):
    """Test quiet hours that cross midnight."""
    user_id = 1009

    # Create user preferences with cross-day quiet hours (22:00 - 07:00)
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.quiet_hours_enabled = True
    prefs.quiet_hours_start = "22:00"
    prefs.quiet_hours_end = "07:00"
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Test evening time (should be in quiet hours)
    test_time = datetime.now(UTC).replace(hour=22, minute=30)
    assert await service.should_send_notification(user_id, "welcome", test_time) is False

    # Test early morning time (should be in quiet hours)
    test_time = datetime.now(UTC).replace(hour=6, minute=30)
    assert await service.should_send_notification(user_id, "welcome", test_time) is False

    # Test daytime time (should be outside quiet hours)
    test_time = datetime.now(UTC).replace(hour=12, minute=0)
    assert await service.should_send_notification(user_id, "welcome", test_time) is True


@pytest.mark.asyncio
async def test_same_day_quiet_hours(async_db: AsyncSession):
    """Test quiet hours within the same day."""
    user_id = 1010

    # Create user preferences with same-day quiet hours (13:00 - 14:00)
    prefs = UserPreferences(user_id=user_id, preferences={})
    async_db.add(prefs)
    await async_db.flush()

    # Update notification preferences
    prefs.quiet_hours_enabled = True
    prefs.quiet_hours_start = "13:00"
    prefs.quiet_hours_end = "14:00"
    await async_db.commit()

    # Create notifications service
    service = NotificationService(async_db)

    # Test within quiet hours
    test_time = datetime.now(UTC).replace(hour=13, minute=30)
    assert await service.should_send_notification(user_id, "welcome", test_time) is False

    # Test before quiet hours
    test_time = datetime.now(UTC).replace(hour=12, minute=30)
    assert await service.should_send_notification(user_id, "welcome", test_time) is True

    # Test after quiet hours
    test_time = datetime.now(UTC).replace(hour=14, minute=30)
    assert await service.should_send_notification(user_id, "welcome", test_time) is True


@pytest.mark.asyncio
async def test_no_user_preferences_defaults_to_send(async_db: AsyncSession):
    """Test that notifications are sent when user has no preferences."""
    user_id = 1011
    tenant_id: UUID = MockUser.DEFAULT_TENANT_ID  # Use UUID

    # Don't create user preferences - simulate new user

    # Create notifications service
    service = NotificationService(async_db)

    # Send email
    notification = await service.send_email(
        tenant_id=tenant_id,
        to_email="test@example.com",
        template_name="welcome",
        context={"name": "Test"},
        user_id=user_id,
    )

    # Verify notification was queued (defaults to sending)
    await async_db.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
