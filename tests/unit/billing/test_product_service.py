"""Unit tests for ProductService operations.

Tests T116: Unit test for ProductService in tests/unit/billing/test_product_service.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def product_data():
    """Sample product data for testing."""
    return {
        "name": "Premium API Access",
        "description": "Full access to all API endpoints",
        "stripe_product_id": "prod_test_123",
        "is_active": True,
    }


@pytest.fixture
def pricing_data():
    """Sample pricing data for testing."""
    return {
        "name": "Monthly Pro",
        "stripe_price_id": "price_test_123",
        "amount_cents": 2999,
        "currency": "usd",
        "interval": "monthly",
        "features": ["Feature 1", "Feature 2"],
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


@pytest.fixture
def mock_tenant():
    """Create a mock tenant context."""
    tenant = MagicMock()
    tenant.id = 1
    return tenant


class TestProductService:
    """Test suite for ProductService operations."""

    @pytest.mark.asyncio
    async def test_create_product(self, mock_db_session, mock_tenant, product_data):
        """Test creating a new product via service."""
        from src.app.modules.billing.schemas import ProductCreate

        mock_product = {
            "id": 1,
            "tenant_id": mock_tenant.id,
            "name": product_data["name"],
            "description": product_data["description"],
            "stripe_product_id": product_data["stripe_product_id"],
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": None,
        }

        with patch(
            "src.app.modules.billing.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.billing.crud.crud_products.create",
                new_callable=AsyncMock,
                return_value=mock_product,
            ):
                from src.app.modules.billing.service import ProductService

                service = ProductService(mock_db_session)
                create_schema = ProductCreate(**product_data)
                result = await service.create_product(create_schema)

                assert result["id"] == 1
                assert result["name"] == product_data["name"]
                assert result["is_active"] is True

    @pytest.mark.asyncio
    async def test_get_product(self, mock_db_session, product_data):
        """Test retrieving a product by ID via service."""
        product_id = 1

        mock_product = {
            "id": product_id,
            "tenant_id": 1,
            "name": product_data["name"],
            "description": product_data["description"],
            "is_active": True,
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            return_value=mock_product,
        ):
            from src.app.modules.billing.service import ProductService

            service = ProductService(mock_db_session)
            result = await service.get_product(product_id)

            assert result["id"] == product_id
            assert result["name"] == product_data["name"]

    @pytest.mark.asyncio
    async def test_get_product_not_found(self, mock_db_session):
        """Test retrieving a non-existent product raises error."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        product_id = 999

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from src.app.modules.billing.service import ProductService

            service = ProductService(mock_db_session)

            with pytest.raises(ModelNotFoundError):
                await service.get_product(product_id)

    @pytest.mark.asyncio
    async def test_get_products_list(self, mock_db_session):
        """Test listing all products."""
        mock_result = {
            "data": [
                {"id": 1, "tenant_id": 1, "name": "Product 1", "is_active": True},
                {"id": 2, "tenant_id": 1, "name": "Product 2", "is_active": True},
            ],
            "total_count": 2,
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get_multi",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            from src.app.modules.billing.service import ProductService

            service = ProductService(mock_db_session)
            result = await service.get_products()

            assert result["data"][0]["name"] == "Product 1"

    @pytest.mark.asyncio
    async def test_update_product(self, mock_db_session, product_data):
        """Test updating a product via service."""
        from src.app.modules.billing.schemas import ProductUpdate

        product_id = 1
        new_name = "Updated Product Name"

        mock_existing = {
            "id": product_id,
            "tenant_id": 1,
            "name": product_data["name"],
            "is_active": True,
        }

        mock_updated = {
            "id": product_id,
            "tenant_id": 1,
            "name": new_name,
            "is_active": True,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            side_effect=[mock_existing, mock_updated],
        ):
            with patch(
                "src.app.modules.billing.crud.crud_products.update",
                new_callable=AsyncMock,
            ):
                from src.app.modules.billing.service import ProductService

                service = ProductService(mock_db_session)
                update_schema = ProductUpdate(name=new_name)
                result = await service.update_product(product_id, update_schema)

                assert result["name"] == new_name

    @pytest.mark.asyncio
    async def test_deactivate_product(self, mock_db_session, product_data):
        """Test deactivating a product via service."""
        product_id = 1

        mock_existing = {
            "id": product_id,
            "tenant_id": 1,
            "name": product_data["name"],
            "is_active": True,
        }

        mock_updated = {
            "id": product_id,
            "tenant_id": 1,
            "name": product_data["name"],
            "is_active": False,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            side_effect=[mock_existing, mock_updated],
        ):
            with patch(
                "src.app.modules.billing.crud.crud_products.update",
                new_callable=AsyncMock,
            ):
                from src.app.modules.billing.service import ProductService

                service = ProductService(mock_db_session)
                result = await service.deactivate_product(product_id)

                assert result["is_active"] is False


class TestProductPricingService:
    """Test suite for Product-Pricing relationship operations."""

    @pytest.mark.asyncio
    async def test_add_pricing_to_product(self, mock_db_session, product_data, pricing_data):
        """Test adding a pricing tier to a product."""
        from src.app.modules.billing.schemas import PricingCreate

        product_id = 1

        mock_product = {
            "id": product_id,
            "tenant_id": 1,
            "name": product_data["name"],
            "is_active": True,
        }

        mock_pricing = {
            "id": 1,
            "product_id": product_id,
            "name": pricing_data["name"],
            "amount_cents": pricing_data["amount_cents"],
            "currency": pricing_data["currency"],
            "interval": pricing_data["interval"],
            "features": pricing_data["features"],
            "is_active": True,
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            return_value=mock_product,
        ):
            with patch(
                "src.app.modules.billing.crud.crud_pricing.create",
                new_callable=AsyncMock,
                return_value=mock_pricing,
            ):
                from src.app.modules.billing.service import ProductService

                service = ProductService(mock_db_session)
                create_schema = PricingCreate(**pricing_data)
                result = await service.add_pricing(product_id, create_schema)

                assert result["id"] == 1
                assert result["product_id"] == product_id
                assert result["amount_cents"] == pricing_data["amount_cents"]

    @pytest.mark.asyncio
    async def test_add_pricing_to_nonexistent_product(self, mock_db_session, pricing_data):
        """Test adding pricing to non-existent product raises error."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.billing.schemas import PricingCreate

        product_id = 999

        with patch(
            "src.app.modules.billing.crud.crud_products.get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from src.app.modules.billing.service import ProductService

            service = ProductService(mock_db_session)
            create_schema = PricingCreate(**pricing_data)

            with pytest.raises(ModelNotFoundError):
                await service.add_pricing(product_id, create_schema)

    @pytest.mark.asyncio
    async def test_get_product_with_pricing(self, mock_db_session, product_data, pricing_data):
        """Test getting a product with its pricing tiers.

        Since get_product_with_pricing uses raw SQLAlchemy with selectinload,
        we need to mock the db.execute() call and return a proper Product model.
        """
        product_id = 1

        # Create mock pricing objects
        mock_pricing1 = MagicMock()
        mock_pricing1.id = 1
        mock_pricing1.product_id = product_id
        mock_pricing1.name = "Monthly"
        mock_pricing1.amount_cents = 999
        mock_pricing1.interval = "monthly"
        mock_pricing1.stripe_price_id = None
        mock_pricing1.currency = "usd"
        mock_pricing1.features = []
        mock_pricing1.is_active = True
        mock_pricing1.created_at = datetime.now(UTC)
        mock_pricing1.updated_at = None

        mock_pricing2 = MagicMock()
        mock_pricing2.id = 2
        mock_pricing2.product_id = product_id
        mock_pricing2.name = "Yearly"
        mock_pricing2.amount_cents = 9999
        mock_pricing2.interval = "yearly"
        mock_pricing2.stripe_price_id = None
        mock_pricing2.currency = "usd"
        mock_pricing2.features = []
        mock_pricing2.is_active = True
        mock_pricing2.created_at = datetime.now(UTC)
        mock_pricing2.updated_at = None

        # Create mock product with pricing_tiers relationship
        mock_product = MagicMock()
        mock_product.id = product_id
        mock_product.tenant_id = 1
        mock_product.name = product_data["name"]
        mock_product.description = product_data.get("description")
        mock_product.stripe_product_id = product_data.get("stripe_product_id")
        mock_product.is_active = True
        mock_product.created_at = datetime.now(UTC)
        mock_product.updated_at = None
        mock_product.pricing_tiers = [mock_pricing1, mock_pricing2]

        # Mock the execute result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_product
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        from src.app.modules.billing.service import ProductService

        service = ProductService(mock_db_session)
        result = await service.get_product_with_pricing(product_id)

        assert result["id"] == product_id
        assert "pricing_tiers" in result
        assert len(result["pricing_tiers"]) == 2
        assert result["pricing_tiers"][0]["interval"] == "monthly"


class TestProductServiceValidation:
    """Test suite for ProductService validation logic."""

    @pytest.mark.asyncio
    async def test_create_product_uses_tenant_context(self, mock_db_session, mock_tenant, product_data):
        """Test that products are created using tenant context."""
        from src.app.modules.billing.schemas import ProductCreate

        mock_product = {
            "id": 1,
            "tenant_id": mock_tenant.id,
            "name": product_data["name"],
            "is_active": True,
        }

        with patch(
            "src.app.modules.billing.service.get_current_tenant",
            return_value=mock_tenant,
        ):
            with patch(
                "src.app.modules.billing.crud.crud_products.create",
                new_callable=AsyncMock,
                return_value=mock_product,
            ) as mock_create:
                from src.app.modules.billing.service import ProductService

                service = ProductService(mock_db_session)
                create_schema = ProductCreate(**product_data)
                result = await service.create_product(create_schema)

                # Verify tenant_id is included
                assert result["tenant_id"] == mock_tenant.id
                mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_filters_by_active(self, mock_db_session):
        """Test listing products with active filter."""
        mock_active_products = {
            "data": [
                {"id": 1, "tenant_id": 1, "name": "Active Product", "is_active": True},
            ],
            "total_count": 1,
        }

        with patch(
            "src.app.modules.billing.crud.crud_products.get_multi",
            new_callable=AsyncMock,
            return_value=mock_active_products,
        ):
            from src.app.modules.billing.service import ProductService

            service = ProductService(mock_db_session)
            result = await service.get_products(is_active=True)

            assert len(result["data"]) == 1
            assert result["data"][0]["is_active"] is True
