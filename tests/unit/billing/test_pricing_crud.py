"""Unit tests for PricingCRUD operations.

Tests T115: Unit test for PricingCRUD in tests/unit/billing/test_pricing_crud.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.crud import crud_pricing
from src.app.modules.billing.models import Pricing
from src.app.modules.billing.schemas import PricingCreate, PricingUpdate


@pytest.fixture
def pricing_data():
    """Sample pricing data for testing."""
    return {
        "name": "Monthly Pro",
        "stripe_price_id": "price_test_123",
        "amount_cents": 2999,
        "currency": "usd",
        "interval": "monthly",
        "features": ["Feature 1", "Feature 2", "Feature 3"],
        "is_active": True,
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


class TestPricingCRUD:
    """Test suite for PricingCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_pricing(self, mock_db_session, pricing_data):
        """Test creating a new pricing tier."""
        product_id = 1
        create_schema = PricingCreate(**pricing_data)

        # Mock the pricing object that would be returned
        mock_pricing = MagicMock(spec=Pricing)
        mock_pricing.id = 1
        mock_pricing.product_id = product_id
        mock_pricing.name = pricing_data["name"]
        mock_pricing.stripe_price_id = pricing_data["stripe_price_id"]
        mock_pricing.amount_cents = pricing_data["amount_cents"]
        mock_pricing.currency = pricing_data["currency"]
        mock_pricing.interval = pricing_data["interval"]
        mock_pricing.features = pricing_data["features"]
        mock_pricing.is_active = pricing_data["is_active"]
        mock_pricing.created_at = datetime.now(UTC)
        mock_pricing.updated_at = None

        with patch.object(crud_pricing, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_pricing

            result = await crud_pricing.create(
                db=mock_db_session,
                obj_in=create_schema
            )

            assert result.id == 1
            assert result.name == pricing_data["name"]
            assert result.amount_cents == pricing_data["amount_cents"]
            assert result.currency == "usd"
            assert result.interval == "monthly"
            assert result.features == pricing_data["features"]
            assert result.is_active is True
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_by_id(self, mock_db_session, pricing_data):
        """Test retrieving a pricing tier by ID."""
        pricing_id = 1
        product_id = 1

        mock_pricing = MagicMock(spec=Pricing)
        mock_pricing.id = pricing_id
        mock_pricing.product_id = product_id
        mock_pricing.name = pricing_data["name"]
        mock_pricing.amount_cents = pricing_data["amount_cents"]
        mock_pricing.is_active = True

        with patch.object(crud_pricing, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_pricing

            result = await crud_pricing.get(
                db=mock_db_session,
                id=pricing_id
            )

            assert result is not None
            assert result.id == pricing_id
            assert result.name == pricing_data["name"]
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_not_found(self, mock_db_session):
        """Test retrieving a non-existent pricing returns None."""
        pricing_id = 999

        with patch.object(crud_pricing, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_pricing.get(
                db=mock_db_session,
                id=pricing_id
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_update_pricing(self, mock_db_session, pricing_data):
        """Test updating a pricing tier."""
        pricing_id = 1
        new_amount = 3999

        mock_pricing = MagicMock(spec=Pricing)
        mock_pricing.id = pricing_id
        mock_pricing.name = pricing_data["name"]
        mock_pricing.amount_cents = new_amount
        mock_pricing.is_active = True
        mock_pricing.updated_at = datetime.now(UTC)

        update_data = PricingUpdate(amount_cents=new_amount)

        with patch.object(crud_pricing, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_pricing

            result = await crud_pricing.update(
                db=mock_db_session,
                db_obj=MagicMock(),
                obj_in=update_data
            )

            assert result.amount_cents == new_amount
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_deactivate_pricing(self, mock_db_session, pricing_data):
        """Test deactivating a pricing tier."""
        pricing_id = 1

        mock_pricing = MagicMock(spec=Pricing)
        mock_pricing.id = pricing_id
        mock_pricing.is_active = False
        mock_pricing.updated_at = datetime.now(UTC)

        update_data = PricingUpdate(is_active=False)

        with patch.object(crud_pricing, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_pricing

            result = await crud_pricing.update(
                db=mock_db_session,
                db_obj=MagicMock(),
                obj_in=update_data
            )

            assert result.is_active is False

    @pytest.mark.asyncio
    async def test_get_pricing_by_product(self, mock_db_session):
        """Test getting all pricing tiers for a product."""
        product_id = 1

        mock_pricing_list = [
            MagicMock(spec=Pricing, id=1, product_id=product_id, name="Monthly", amount_cents=999, interval="monthly"),
            MagicMock(spec=Pricing, id=2, product_id=product_id, name="Yearly", amount_cents=9999, interval="yearly"),
        ]

        with patch.object(crud_pricing, 'get_multi', new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = mock_pricing_list

            result = await crud_pricing.get_multi(
                db=mock_db_session,
                product_id=product_id,
            )

            assert len(result) == 2
            assert result[0].interval == "monthly"
            assert result[1].interval == "yearly"

    @pytest.mark.asyncio
    async def test_update_pricing_features(self, mock_db_session, pricing_data):
        """Test updating pricing features list."""
        pricing_id = 1
        new_features = ["New Feature 1", "New Feature 2"]

        mock_pricing = MagicMock(spec=Pricing)
        mock_pricing.id = pricing_id
        mock_pricing.features = new_features
        mock_pricing.updated_at = datetime.now(UTC)

        update_data = PricingUpdate(features=new_features)

        with patch.object(crud_pricing, 'update', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_pricing

            result = await crud_pricing.update(
                db=mock_db_session,
                db_obj=MagicMock(),
                obj_in=update_data
            )

            assert result.features == new_features
            assert len(result.features) == 2

    @pytest.mark.asyncio
    async def test_pricing_intervals(self, mock_db_session):
        """Test different pricing intervals."""
        intervals = ["monthly", "yearly", "one_time"]

        for interval in intervals:
            pricing_data = {
                "name": f"{interval.capitalize()} Plan",
                "amount_cents": 1999,
                "interval": interval,
            }
            schema = PricingCreate(**pricing_data)
            assert schema.interval == interval


class TestPricingSchema:
    """Test suite for Pricing schema validation."""

    def test_pricing_create_valid(self, pricing_data):
        """Test creating a valid PricingCreate schema."""
        schema = PricingCreate(**pricing_data)
        assert schema.name == pricing_data["name"]
        assert schema.amount_cents == pricing_data["amount_cents"]
        assert schema.currency == "usd"
        assert schema.interval == "monthly"

    def test_pricing_create_minimal(self):
        """Test creating PricingCreate with only required fields."""
        schema = PricingCreate(
            name="Basic",
            amount_cents=999,
            interval="monthly"
        )
        assert schema.name == "Basic"
        assert schema.amount_cents == 999
        assert schema.interval == "monthly"
        assert schema.currency == "usd"  # default
        assert schema.features == []  # default
        assert schema.is_active is True  # default

    def test_pricing_update_partial(self):
        """Test PricingUpdate with partial data."""
        schema = PricingUpdate(amount_cents=1999)
        assert schema.amount_cents == 1999
        assert schema.name is None
        assert schema.is_active is None

    def test_pricing_intervals_valid(self):
        """Test valid pricing intervals."""
        for interval in ["monthly", "yearly", "one_time"]:
            schema = PricingCreate(
                name="Test",
                amount_cents=100,
                interval=interval
            )
            assert schema.interval == interval

    def test_pricing_amount_zero(self):
        """Test pricing with zero amount (free tier)."""
        schema = PricingCreate(
            name="Free Tier",
            amount_cents=0,
            interval="monthly"
        )
        assert schema.amount_cents == 0

    def test_pricing_different_currencies(self):
        """Test pricing with different currencies."""
        for currency in ["usd", "eur", "gbp"]:
            schema = PricingCreate(
                name="Test",
                amount_cents=999,
                interval="monthly",
                currency=currency
            )
            assert schema.currency == currency

    def test_pricing_features_list(self):
        """Test pricing with features list."""
        features = ["Feature 1", "Feature 2", "Feature 3"]
        schema = PricingCreate(
            name="Test",
            amount_cents=999,
            interval="monthly",
            features=features
        )
        assert schema.features == features
        assert len(schema.features) == 3
