"""Integration tests for notification preferences with notifications service.

Tests verify that user preferences are respected when sending notifications.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import NotificationService
from src.app.modules.user_preferences.models import UserPreferences
from tests.conftest import MockUser


@pytest.fixture
async def async_db(setup_db):
    """Async database session for testing."""
    from tests.conftest import get_testing_session_local

    async with get_testing_session_local()() as session:
        yield session


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
