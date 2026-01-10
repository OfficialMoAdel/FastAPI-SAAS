"""Unit tests for billing service layer.

TDD tests for Stripe integration, subscription management,
and billing record operations.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestStripeSettingsService:
    """Test Stripe settings service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_get_settings_returns_none_when_no_tenant(self, mock_db):
        """Test that get_settings returns None when no tenant context."""
        from src.app.modules.billing.service import StripeSettingsService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=None):
            service = StripeSettingsService(mock_db)
            result = await service.get_settings()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_settings_queries_by_tenant_id(self, mock_db, mock_tenant_context):
        """Test that get_settings queries with tenant_id."""
        from src.app.modules.billing.service import StripeSettingsService

        expected_settings = {
            "id": 1,
            "tenant_id": 1,
            "stripe_customer_id": "cus_test123",
            "is_active": True,
        }

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_stripe_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=expected_settings)

                service = StripeSettingsService(mock_db)
                result = await service.get_settings()

                mock_crud.get.assert_called_once_with(db=mock_db, tenant_id=1)
                assert result == expected_settings

    @pytest.mark.asyncio
    async def test_update_settings_encrypts_secret_key(self, mock_db, mock_tenant_context):
        """Test that update_settings encrypts the Stripe secret key."""
        from src.app.modules.billing.schemas import StripeSettingsUpdate
        from src.app.modules.billing.service import StripeSettingsService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_stripe_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value={"id": 1, "tenant_id": 1})

                service = StripeSettingsService(mock_db)
                service._encryption = MagicMock()
                service._encryption.encrypt = MagicMock(return_value="encrypted_key")

                data = StripeSettingsUpdate(stripe_secret_key="sk_test_123")
                await service.update_settings(data)

                # Verify encryption was called
                service._encryption.encrypt.assert_called_once_with("sk_test_123")

    @pytest.mark.asyncio
    async def test_update_settings_creates_new_when_none_exists(self, mock_db, mock_tenant_context):
        """Test that update_settings creates new settings if none exist."""
        from src.app.modules.billing.schemas import StripeSettingsUpdate
        from src.app.modules.billing.service import StripeSettingsService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_stripe_settings") as mock_crud:
                mock_crud.get = AsyncMock(return_value=None)
                mock_crud.create = AsyncMock(return_value={"id": 1, "tenant_id": 1})

                service = StripeSettingsService(mock_db)
                data = StripeSettingsUpdate(is_active=True)
                await service.update_settings(data)

                mock_crud.create.assert_called_once()
                call_args = mock_crud.create.call_args
                assert call_args.kwargs["object"].tenant_id == 1


class TestSubscriptionPlanService:
    """Test subscription plan service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_create_plan_injects_tenant_id(self, mock_db, mock_tenant_context):
        """Test that create_plan injects tenant_id from context."""
        from src.app.modules.billing.schemas import SubscriptionPlanCreate
        from src.app.modules.billing.service import SubscriptionPlanService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_subscription_plans") as mock_crud:
                mock_crud.create = AsyncMock(return_value={"id": 1, "tenant_id": 1, "name": "Pro"})

                service = SubscriptionPlanService(mock_db)
                data = SubscriptionPlanCreate(
                    name="Pro",
                    price_monthly_cents=1999,
                    price_yearly_cents=19990,
                )
                await service.create_plan(data)

                call_args = mock_crud.create.call_args
                assert call_args.kwargs["object"].tenant_id == 1

    @pytest.mark.asyncio
    async def test_get_plan_raises_not_found(self, mock_db):
        """Test that get_plan raises ModelNotFoundError when plan doesn't exist."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.billing.service import SubscriptionPlanService

        with patch("src.app.modules.billing.service.crud_subscription_plans") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            service = SubscriptionPlanService(mock_db)

            with pytest.raises(ModelNotFoundError):
                await service.get_plan(999)

    @pytest.mark.asyncio
    async def test_get_plans_with_active_filter(self, mock_db):
        """Test that get_plans applies is_active filter."""
        from src.app.modules.billing.service import SubscriptionPlanService

        with patch("src.app.modules.billing.service.crud_subscription_plans") as mock_crud:
            mock_crud.get_multi = AsyncMock(return_value={"data": [], "total_count": 0})

            service = SubscriptionPlanService(mock_db)
            await service.get_plans(page=1, limit=20, is_active=True)

            call_args = mock_crud.get_multi.call_args
            assert call_args.kwargs["is_active"] is True

    @pytest.mark.asyncio
    async def test_delete_plan_deactivates_instead_of_delete(self, mock_db):
        """Test that delete_plan sets is_active=False (soft delete)."""
        from src.app.modules.billing.service import SubscriptionPlanService

        with patch("src.app.modules.billing.service.crud_subscription_plans") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "name": "Pro"})
            mock_crud.update = AsyncMock()

            service = SubscriptionPlanService(mock_db)
            result = await service.delete_plan(1)

            call_args = mock_crud.update.call_args
            assert call_args.kwargs["object"]["is_active"] is False
            assert "message" in result


class TestProductService:
    """Test product service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_create_product_injects_tenant_id(self, mock_db, mock_tenant_context):
        """Test that create_product injects tenant_id from context."""
        from src.app.modules.billing.schemas import ProductCreate
        from src.app.modules.billing.service import ProductService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_products") as mock_crud:
                mock_crud.create = AsyncMock(return_value={"id": 1, "tenant_id": 1, "name": "Widget"})

                service = ProductService(mock_db)
                data = ProductCreate(name="Widget")
                await service.create_product(data)

                call_args = mock_crud.create.call_args
                assert call_args.kwargs["object"].tenant_id == 1

    @pytest.mark.asyncio
    async def test_get_product_raises_not_found(self, mock_db):
        """Test that get_product raises ModelNotFoundError when product doesn't exist."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.billing.service import ProductService

        with patch("src.app.modules.billing.service.crud_products") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            service = ProductService(mock_db)

            with pytest.raises(ModelNotFoundError):
                await service.get_product(999)


class TestUserSubscriptionService:
    """Test user subscription service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_cancel_subscription_sets_cancel_at_period_end(self, mock_db):
        """Test that cancel_subscription sets cancel_at_period_end=True."""
        from src.app.modules.billing.service import UserSubscriptionService

        with patch("src.app.modules.billing.service.crud_user_subscriptions") as mock_crud:
            mock_crud.get = AsyncMock(return_value={"id": 1, "user_id": 1, "status": "active"})
            mock_crud.update = AsyncMock()

            service = UserSubscriptionService(mock_db)
            await service.cancel_subscription(1)

            call_args = mock_crud.update.call_args
            assert call_args.kwargs["object"]["cancel_at_period_end"] is True
            assert call_args.kwargs["object"]["canceled_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel_subscription_raises_not_found(self, mock_db):
        """Test that cancel_subscription raises ModelNotFoundError when subscription doesn't exist."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.billing.service import UserSubscriptionService

        with patch("src.app.modules.billing.service.crud_user_subscriptions") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            service = UserSubscriptionService(mock_db)

            with pytest.raises(ModelNotFoundError):
                await service.cancel_subscription(999)


class TestBillingRecordService:
    """Test billing record service operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_tenant_context(self):
        """Create a mock tenant context."""
        tenant = MagicMock()
        tenant.id = 1
        return tenant

    @pytest.mark.asyncio
    async def test_create_record_injects_tenant_id(self, mock_db, mock_tenant_context):
        """Test that create_record injects tenant_id from context."""
        from src.app.modules.billing.schemas import BillingRecordCreate
        from src.app.modules.billing.service import BillingRecordService

        with patch("src.app.modules.billing.service.get_tenant_context", return_value=mock_tenant_context):
            with patch("src.app.modules.billing.service.crud_billing_records") as mock_crud:
                mock_crud.create = AsyncMock(return_value={"id": 1, "tenant_id": 1})

                service = BillingRecordService(mock_db)
                data = BillingRecordCreate(
                    user_id=1,
                    amount_cents=1999,
                    status="succeeded",
                )
                await service.create_record(data)

                call_args = mock_crud.create.call_args
                assert call_args.kwargs["object"].tenant_id == 1

    @pytest.mark.asyncio
    async def test_get_user_records_paginates_correctly(self, mock_db):
        """Test that get_user_records applies pagination correctly."""
        from src.app.modules.billing.service import BillingRecordService

        with patch("src.app.modules.billing.service.crud_billing_records") as mock_crud:
            mock_crud.get_multi = AsyncMock(return_value={"data": [], "total_count": 0})

            service = BillingRecordService(mock_db)
            await service.get_user_records(user_id=1, page=2, limit=10)

            call_args = mock_crud.get_multi.call_args
            assert call_args.kwargs["offset"] == 10  # (page-1) * limit = (2-1) * 10
            assert call_args.kwargs["limit"] == 10
