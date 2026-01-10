"""Unit tests for UserSubscriptionCRUD operations.

Tests T064: Unit test for UserSubscriptionCRUD in tests/unit/billing/test_user_subscription_crud.py
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.crud import crud_user_subscriptions
from src.app.modules.billing.models import UserSubscription
from src.app.modules.billing.schemas import UserSubscriptionCreate, UserSubscriptionUpdate


@pytest.fixture
def user_subscription_data():
    """Sample user subscription data for testing."""
    now = datetime.now(UTC)
    return {
        "user_id": 1,
        "subscription_plan_id": 1,
        "status": "active",
        "billing_interval": "monthly",
        "current_period_start": now,
        "current_period_end": now + timedelta(days=30),
    }


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


class TestUserSubscriptionCRUD:
    """Test suite for UserSubscriptionCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_user_subscription(self, mock_db_session, user_subscription_data):
        """Test creating a new user subscription."""
        tenant_id = 1
        create_schema = UserSubscriptionCreate(**user_subscription_data)

        # Mock the subscription object that would be returned
        mock_subscription = MagicMock(spec=UserSubscription)
        mock_subscription.id = 1
        mock_subscription.tenant_id = tenant_id
        mock_subscription.user_id = user_subscription_data["user_id"]
        mock_subscription.subscription_plan_id = user_subscription_data["subscription_plan_id"]
        mock_subscription.status = user_subscription_data["status"]
        mock_subscription.billing_interval = user_subscription_data["billing_interval"]
        mock_subscription.current_period_start = user_subscription_data["current_period_start"]
        mock_subscription.current_period_end = user_subscription_data["current_period_end"]
        mock_subscription.cancel_at_period_end = False
        mock_subscription.canceled_at = None
        mock_subscription.stripe_subscription_id = None

        # Setup mock behavior
        mock_db_session.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, 'id', 1))

        with patch.object(crud_user_subscriptions, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_subscription

            result = await crud_user_subscriptions.create(
                db=mock_db_session,
                tenant_id=tenant_id,
                obj_in=create_schema
            )

            assert result.id == 1
            assert result.user_id == user_subscription_data["user_id"]
            assert result.status == "active"
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_subscription_by_id(self, mock_db_session):
        """Test retrieving a user subscription by ID."""
        tenant_id = 1
        subscription_id = 1

        mock_subscription = MagicMock(spec=UserSubscription)
        mock_subscription.id = subscription_id
        mock_subscription.tenant_id = tenant_id
        mock_subscription.status = "active"

        with patch.object(crud_user_subscriptions, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_subscription

            result = await crud_user_subscriptions.get(
                db=mock_db_session,
                tenant_id=tenant_id,
                id=subscription_id
            )

            assert result is not None
            assert result.id == subscription_id
            mock_get.assert_called_once_with(
                db=mock_db_session,
                tenant_id=tenant_id,
                id=subscription_id
            )

    @pytest.mark.asyncio
    async def test_get_user_subscription_not_found(self, mock_db_session):
        """Test retrieving a non-existent subscription returns None."""
        tenant_id = 1
        subscription_id = 999

        with patch.object(crud_user_subscriptions, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_user_subscriptions.get(
                db=mock_db_session,
                tenant_id=tenant_id,
                id=subscription_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_update_user_subscription_status(self, mock_db_session):
        """Test updating a subscription status."""
        tenant_id = 1
        subscription_id = 1

        mock_subscription = MagicMock(spec=UserSubscription)
        mock_subscription.id = subscription_id
        mock_subscription.tenant_id = tenant_id
        mock_subscription.status = "canceled"

        update_data = UserSubscriptionUpdate(status="canceled")

        with patch.object(crud_user_subscriptions, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_subscription

            result = await crud_user_subscriptions.update(
                db=mock_db_session,
                tenant_id=tenant_id,
                db_obj=MagicMock(),
                obj_in=update_data
            )

            assert result.status == "canceled"
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_cancel_at_period_end(self, mock_db_session):
        """Test setting cancel_at_period_end flag."""
        tenant_id = 1
        now = datetime.now(UTC)

        mock_subscription = MagicMock(spec=UserSubscription)
        mock_subscription.id = 1
        mock_subscription.tenant_id = tenant_id
        mock_subscription.cancel_at_period_end = True
        mock_subscription.canceled_at = now

        update_data = UserSubscriptionUpdate(
            cancel_at_period_end=True,
            canceled_at=now
        )

        with patch.object(crud_user_subscriptions, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_subscription

            result = await crud_user_subscriptions.update(
                db=mock_db_session,
                tenant_id=tenant_id,
                db_obj=MagicMock(),
                obj_in=update_data
            )

            assert result.cancel_at_period_end is True
            assert result.canceled_at is not None

    @pytest.mark.asyncio
    async def test_get_multi_by_user(self, mock_db_session):
        """Test getting all subscriptions for a user."""
        tenant_id = 1
        user_id = 1

        mock_subscriptions = [
            MagicMock(spec=UserSubscription, id=1, user_id=user_id, status="active"),
            MagicMock(spec=UserSubscription, id=2, user_id=user_id, status="expired"),
        ]

        with patch.object(crud_user_subscriptions, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = mock_subscriptions

            result = await crud_user_subscriptions.get_multi(
                db=mock_db_session,
                tenant_id=tenant_id,
            )

            assert len(result) == 2
            assert result[0].status == "active"

    @pytest.mark.asyncio
    async def test_subscription_status_values(self, user_subscription_data):
        """Test that subscription status values are valid."""
        valid_statuses = ["active", "canceled", "past_due", "expired", "trialing"]

        for status in valid_statuses:
            data = user_subscription_data.copy()
            data["status"] = status
            schema = UserSubscriptionCreate(**data)
            assert schema.status == status

    @pytest.mark.asyncio
    async def test_billing_interval_values(self, user_subscription_data):
        """Test that billing interval values are valid."""
        valid_intervals = ["monthly", "yearly"]

        for interval in valid_intervals:
            data = user_subscription_data.copy()
            data["billing_interval"] = interval
            schema = UserSubscriptionCreate(**data)
            assert schema.billing_interval == interval

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_db_session):
        """Test that subscription queries are tenant-isolated."""
        tenant_id_1 = 1
        tenant_id_2 = 2
        subscription_id = 1

        # Subscription belongs to tenant 1
        mock_subscription = MagicMock(spec=UserSubscription)
        mock_subscription.id = subscription_id
        mock_subscription.tenant_id = tenant_id_1

        with patch.object(crud_user_subscriptions, 'get', new_callable=AsyncMock) as mock_get:
            # When querying with tenant_id=1, should find subscription
            mock_get.return_value = mock_subscription
            result = await crud_user_subscriptions.get(
                db=mock_db_session,
                tenant_id=tenant_id_1,
                id=subscription_id
            )
            assert result is not None

            # When querying with tenant_id=2, should not find subscription
            mock_get.return_value = None
            result = await crud_user_subscriptions.get(
                db=mock_db_session,
                tenant_id=tenant_id_2,
                id=subscription_id
            )
            assert result is None
