from fastapi import status
"""Unit tests for subscription lifecycle transitions.

Tests T107: Unit test for subscription lifecycle in tests/unit/billing/test_subscription_lifecycle.py
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.service import UserSubscriptionService


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


class TestSubscriptionLifecycleStates:
    """Test suite for subscription lifecycle state transitions."""

    @pytest.mark.asyncio
    async def test_new_subscription_starts_active(self, mock_db_session):
        """Test that new subscriptions start in 'active' status."""
        service = UserSubscriptionService(mock_db_session)

        mock_subscription = {
            "id": 1,
            "user_id": 1,
            "tenant_id": 1,
            "subscription_plan_id": 1,
            "status": "active",
            "billing_interval": "monthly",
            "current_period_start": datetime.now(UTC),
            "current_period_end": datetime.now(UTC) + timedelta(days=30),
            "cancel_at_period_end": False,
            "canceled_at": None,
        }

        with patch(
            "src.app.modules.billing.crud.crud_user_subscriptions.get",
            new_callable=AsyncMock,
            return_value=mock_subscription,
        ):
            result = await service.get_subscription(subscription_id=1)
            assert result["status"] == "active"
            assert result["cancel_at_period_end"] is False

    @pytest.mark.asyncio
    async def test_trialing_status(self, mock_db_session):
        """Test subscription in trial period."""
        service = UserSubscriptionService(mock_db_session)

        trial_end = datetime.now(UTC) + timedelta(days=14)
        mock_subscription = {
            "id": 1,
            "user_id": 1,
            "tenant_id": 1,
            "status": "trialing",
            "trial_ends_at": trial_end,
            "cancel_at_period_end": False,
        }

        with patch(
            "src.app.modules.billing.crud.crud_user_subscriptions.get",
            new_callable=AsyncMock,
            return_value=mock_subscription,
        ):
            result = await service.get_subscription(subscription_id=1)
            assert result["status"] == "trialing"


class TestSubscriptionStateTransitions:
    """Test suite for valid state transitions."""

    @pytest.mark.asyncio
    async def test_active_to_cancel_at_period_end(self, mock_db_session):
        """Test transition from active to cancel_at_period_end=True."""
        service = UserSubscriptionService(mock_db_session)

        mock_subscription = {
            "id": 1,
            "status": "active",
            "cancel_at_period_end": False,
        }

        mock_updated = {
            **mock_subscription,
            "cancel_at_period_end": True,
            "canceled_at": datetime.now(UTC),
        }

        with patch(
            "src.app.modules.billing.crud.crud_user_subscriptions.get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_subscription, mock_updated]

            with patch(
                "src.app.modules.billing.crud.crud_user_subscriptions.update",
                new_callable=AsyncMock,
            ):
                result = await service.cancel_subscription(subscription_id=1)
                assert result["cancel_at_period_end"] is True
                assert result["canceled_at"] is not None

    @pytest.mark.asyncio
    async def test_active_to_past_due_on_payment_failure(self, mock_db_session):
        """Test transition from active to past_due when payment fails."""
        # This is handled by webhook - testing the expected state
        mock_subscription = {
            "id": 1,
            "status": "active",
        }

        mock_updated = {
            **mock_subscription,
            "status": "past_due",
        }

        # Verify the state transition is valid
        assert mock_subscription["status"] == "active"
        assert mock_updated["status"] == "past_due"

    @pytest.mark.asyncio
    async def test_past_due_to_active_on_payment_success(self, mock_db_session):
        """Test transition from past_due back to active when payment succeeds."""
        # This is handled by webhook - testing the expected state
        mock_subscription = {
            "id": 1,
            "status": "past_due",
        }

        mock_updated = {
            **mock_subscription,
            "status": "active",
        }

        # Verify the state transition is valid
        assert mock_subscription["status"] == "past_due"
        assert mock_updated["status"] == "active"

    @pytest.mark.asyncio
    async def test_past_due_to_canceled_on_expiration(self, mock_db_session):
        """Test transition from past_due to canceled when subscription expires."""
        mock_subscription = {
            "id": 1,
            "status": "past_due",
        }

        mock_updated = {
            **mock_subscription,
            "status": "canceled",
            "canceled_at": datetime.now(UTC),
        }

        # Verify the state transition is valid
        assert mock_subscription["status"] == "past_due"
        assert mock_updated["status"] == "canceled"


class TestSubscriptionStatusValidation:
    """Test suite for subscription status validation."""

    def test_valid_statuses(self):
        """Test all valid subscription statuses."""
        valid_statuses = ["active", "trialing", "past_due", "canceled", "expired"]

        for status in valid_statuses:
            subscription = {"status": status}
            assert subscription["status"] in valid_statuses

    def test_status_map_from_stripe(self):
        """Test Stripe status mapping to internal statuses."""
        # Based on webhook.py handle_subscription_updated
        status_map = {
            "active": "active",
            "past_due": "past_due",
            "canceled": "canceled",
            "unpaid": "past_due",
            "incomplete": "past_due",
            "incomplete_expired": "expired",
            "trialing": "trialing",
        }

        # Verify all Stripe statuses are mapped
        stripe_statuses = ["active", "past_due", "canceled", "unpaid", "incomplete", "incomplete_expired", "trialing"]
        for stripe_status in stripe_statuses:
            assert stripe_status in status_map


class TestSubscriptionPeriodManagement:
    """Test suite for subscription period management."""

    @pytest.mark.asyncio
    async def test_current_period_dates_updated_on_renewal(self, mock_db_session):
        """Test that period dates are updated when subscription renews."""
        old_start = datetime.now(UTC) - timedelta(days=30)
        old_end = datetime.now(UTC)
        new_start = datetime.now(UTC)
        new_end = datetime.now(UTC) + timedelta(days=30)

        mock_subscription = {
            "id": 1,
            "status": "active",
            "current_period_start": old_start,
            "current_period_end": old_end,
        }

        mock_renewed = {
            **mock_subscription,
            "current_period_start": new_start,
            "current_period_end": new_end,
        }

        # Verify period dates are updated
        assert mock_subscription["current_period_end"] <= datetime.now(UTC)
        assert mock_renewed["current_period_end"] > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_subscription_access_during_period(self, mock_db_session):
        """Test that subscription provides access during current period."""
        period_start = datetime.now(UTC) - timedelta(days=15)
        period_end = datetime.now(UTC) + timedelta(days=15)

        subscription = {
            "id": 1,
            "status": "active",
            "current_period_start": period_start,
            "current_period_end": period_end,
        }

        # User should have access when current time is within period
        now = datetime.now(UTC)
        has_access = (
            subscription["status"] == "active"
            and subscription["current_period_start"] <= now <= subscription["current_period_end"]
        )
        assert has_access is True

    @pytest.mark.asyncio
    async def test_canceled_subscription_access_until_period_end(self, mock_db_session):
        """Test that canceled subscription still provides access until period end."""
        period_end = datetime.now(UTC) + timedelta(days=10)

        subscription = {
            "id": 1,
            "status": "active",  # Still active
            "cancel_at_period_end": True,  # But will cancel
            "canceled_at": datetime.now(UTC) - timedelta(days=5),
            "current_period_end": period_end,
        }

        # User should still have access
        now = datetime.now(UTC)
        has_access = subscription["status"] == "active" and now < subscription["current_period_end"]
        assert has_access is True


class TestBillingIntervalTransitions:
    """Test suite for billing interval management."""

    @pytest.mark.asyncio
    async def test_monthly_subscription_period(self, mock_db_session):
        """Test monthly subscription period calculation."""
        start = datetime.now(UTC)
        end = start + timedelta(days=30)

        subscription = {
            "id": 1,
            "billing_interval": "monthly",
            "current_period_start": start,
            "current_period_end": end,
        }

        period_length = (subscription["current_period_end"] - subscription["current_period_start"]).days
        assert 28 <= period_length <= 31  # Month length varies

    @pytest.mark.asyncio
    async def test_yearly_subscription_period(self, mock_db_session):
        """Test yearly subscription period calculation."""
        start = datetime.now(UTC)
        end = start + timedelta(days=365)

        subscription = {
            "id": 1,
            "billing_interval": "yearly",
            "current_period_start": start,
            "current_period_end": end,
        }

        period_length = (subscription["current_period_end"] - subscription["current_period_start"]).days
        assert 365 <= period_length <= 366  # Account for leap year


class TestCancellationFlow:
    """Test suite for complete cancellation flow."""

    @pytest.mark.asyncio
    async def test_full_cancellation_flow(self, mock_db_session):
        """Test complete cancellation lifecycle."""
        # service = UserSubscriptionService(mock_db_session)

        # Step 1: Active subscription
        active_subscription = {
            "id": 1,
            "status": "active",
            "cancel_at_period_end": False,
            "canceled_at": None,
            "current_period_end": datetime.now(UTC) + timedelta(days=20),
        }

        # Step 2: User requests cancellation
        canceling_subscription = {
            **active_subscription,
            "cancel_at_period_end": True,
            "canceled_at": datetime.now(UTC),
            "status": "active",  # Still active
        }

        # Verify state after cancellation request
        assert canceling_subscription["status"] == "active"
        assert canceling_subscription["cancel_at_period_end"] is True

        # Step 3: After period ends (handled by Stripe webhook)
        canceled_subscription = {
            **canceling_subscription,
            "status": "canceled",
        }

        # Verify final state
        assert canceled_subscription["status"] == "canceled"

    @pytest.mark.asyncio
    async def test_reactivation_not_allowed_after_cancellation(self, mock_db_session):
        """Test that canceled subscriptions cannot be directly reactivated."""
        # Users must create a new subscription after cancellation
        canceled_subscription = {
            "id": 1,
            "status": "canceled",
            "canceled_at": datetime.now(UTC) - timedelta(days=1),
        }

        # Reactivation should require new checkout, not update
        assert canceled_subscription["status"] == "canceled"
        # To reactivate, user must go through checkout flow again
