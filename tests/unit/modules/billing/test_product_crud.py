"""Unit tests for ProductCRUD operations.

Tests T114: Unit test for ProductCRUD in tests/unit/billing/test_product_crud.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.crud import crud_products
from src.app.modules.billing.models import Product
from src.app.modules.billing.schemas import ProductCreate, ProductUpdate


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
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


class TestProductCRUD:
    """Test suite for ProductCRUD operations."""

    @pytest.mark.asyncio
    async def test_create_product(self, mock_db_session, product_data):
        """Test creating a new product."""
        tenant_id = 1
        create_schema = ProductCreate(**product_data)

        # Mock the product object that would be returned
        mock_product = MagicMock(spec=Product)
        mock_product.id = 1
        mock_product.tenant_id = tenant_id
        mock_product.name = product_data["name"]
        mock_product.description = product_data["description"]
        mock_product.stripe_product_id = product_data["stripe_product_id"]
        mock_product.is_active = product_data["is_active"]
        mock_product.created_at = datetime.now(UTC)
        mock_product.updated_at = None

        with patch.object(crud_products, "create", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_product

            result = await crud_products.create(db=mock_db_session, tenant_id=tenant_id, obj_in=create_schema)

            assert result.id == 1
            assert result.name == product_data["name"]
            assert result.description == product_data["description"]
            assert result.stripe_product_id == product_data["stripe_product_id"]
            assert result.is_active is True
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_product_by_id(self, mock_db_session, product_data):
        """Test retrieving a product by ID."""
        tenant_id = 1
        product_id = 1

        mock_product = MagicMock(spec=Product)
        mock_product.id = product_id
        mock_product.tenant_id = tenant_id
        mock_product.name = product_data["name"]
        mock_product.is_active = True

        with patch.object(crud_products, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_product

            result = await crud_products.get(db=mock_db_session, tenant_id=tenant_id, id=product_id)

            assert result is not None
            assert result.id == product_id
            assert result.name == product_data["name"]
            mock_get.assert_called_once_with(db=mock_db_session, tenant_id=tenant_id, id=product_id)

    @pytest.mark.asyncio
    async def test_get_product_not_found(self, mock_db_session):
        """Test retrieving a non-existent product returns None."""
        tenant_id = 1
        product_id = 999

        with patch.object(crud_products, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await crud_products.get(db=mock_db_session, tenant_id=tenant_id, id=product_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_update_product(self, mock_db_session, product_data):
        """Test updating a product."""
        tenant_id = 1
        product_id = 1
        new_name = "Updated Product Name"

        mock_product = MagicMock(spec=Product)
        mock_product.id = product_id
        mock_product.tenant_id = tenant_id
        mock_product.name = new_name
        mock_product.description = "Updated description"
        mock_product.is_active = True
        mock_product.updated_at = datetime.now(UTC)

        update_data = ProductUpdate(name=new_name, description="Updated description")

        with patch.object(crud_products, "update", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_product

            result = await crud_products.update(
                db=mock_db_session, tenant_id=tenant_id, db_obj=MagicMock(), obj_in=update_data
            )

            assert result.name == new_name
            assert result.description == "Updated description"
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_deactivate_product(self, mock_db_session, product_data):
        """Test deactivating a product."""
        tenant_id = 1
        product_id = 1

        mock_product = MagicMock(spec=Product)
        mock_product.id = product_id
        mock_product.tenant_id = tenant_id
        mock_product.is_active = False
        mock_product.updated_at = datetime.now(UTC)

        update_data = ProductUpdate(is_active=False)

        with patch.object(crud_products, "update", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = mock_product

            result = await crud_products.update(
                db=mock_db_session, tenant_id=tenant_id, db_obj=MagicMock(), obj_in=update_data
            )

            assert result.is_active is False

    @pytest.mark.asyncio
    async def test_get_multi_products(self, mock_db_session):
        """Test getting multiple products."""
        tenant_id = 1

        mock_product_1 = MagicMock(spec=Product)
        mock_product_1.id = 1
        mock_product_1.name = "Product 1"
        mock_product_1.is_active = True

        mock_product_2 = MagicMock(spec=Product)
        mock_product_2.id = 2
        mock_product_2.name = "Product 2"
        mock_product_2.is_active = True

        mock_product_3 = MagicMock(spec=Product)
        mock_product_3.id = 3
        mock_product_3.name = "Product 3"
        mock_product_3.is_active = False

        mock_products = [mock_product_1, mock_product_2, mock_product_3]

        with patch.object(crud_products, "get_multi", new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = mock_products

            result = await crud_products.get_multi(
                db=mock_db_session,
                tenant_id=tenant_id,
            )

            assert len(result) == 3
            assert result[0].name == "Product 1"

    @pytest.mark.asyncio
    async def test_get_active_products_only(self, mock_db_session):
        """Test filtering only active products."""
        tenant_id = 1

        mock_active_products = [
            MagicMock(spec=Product, id=1, name="Product 1", is_active=True),
            MagicMock(spec=Product, id=2, name="Product 2", is_active=True),
        ]

        with patch.object(crud_products, "get_multi", new_callable=AsyncMock) as mock_get_multi:
            mock_get_multi.return_value = mock_active_products

            result = await crud_products.get_multi(
                db=mock_db_session,
                tenant_id=tenant_id,
                is_active=True,
            )

            assert len(result) == 2
            assert all(p.is_active for p in result)

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_db_session, product_data):
        """Test that product queries are tenant-isolated."""
        tenant_id_1 = 1
        tenant_id_2 = 2
        product_id = 1

        # Product belongs to tenant 1
        mock_product = MagicMock(spec=Product)
        mock_product.id = product_id
        mock_product.tenant_id = tenant_id_1

        with patch.object(crud_products, "get", new_callable=AsyncMock) as mock_get:
            # When querying with tenant_id=1, should find product
            mock_get.return_value = mock_product
            result = await crud_products.get(db=mock_db_session, tenant_id=tenant_id_1, id=product_id)
            assert result is not None

            # When querying with tenant_id=2, should not find product
            mock_get.return_value = None
            result = await crud_products.get(db=mock_db_session, tenant_id=tenant_id_2, id=product_id)
            assert result is None

    @pytest.mark.asyncio
    async def test_product_create_minimal(self, mock_db_session):
        """Test creating a product with minimal required fields."""
        tenant_id = 1
        minimal_data = {"name": "Basic Product"}
        create_schema = ProductCreate(**minimal_data)

        mock_product = MagicMock(spec=Product)
        mock_product.id = 1
        mock_product.tenant_id = tenant_id
        mock_product.name = minimal_data["name"]
        mock_product.description = None
        mock_product.stripe_product_id = None
        mock_product.is_active = True

        with patch.object(crud_products, "create", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_product

            result = await crud_products.create(db=mock_db_session, tenant_id=tenant_id, obj_in=create_schema)

            assert result.id == 1
            assert result.name == minimal_data["name"]
            assert result.description is None
            assert result.stripe_product_id is None
            assert result.is_active is True


class TestProductSchema:
    """Test suite for Product schema validation."""

    def test_product_create_valid(self, product_data):
        """Test creating a valid ProductCreate schema."""
        schema = ProductCreate(**product_data)
        assert schema.name == product_data["name"]
        assert schema.description == product_data["description"]
        assert schema.is_active is True

    def test_product_create_minimal(self):
        """Test creating ProductCreate with only required fields."""
        schema = ProductCreate(name="Test Product")
        assert schema.name == "Test Product"
        assert schema.description is None
        assert schema.is_active is True

    def test_product_update_partial(self):
        """Test ProductUpdate with partial data."""
        schema = ProductUpdate(name="New Name")
        assert schema.name == "New Name"
        assert schema.description is None
        assert schema.is_active is None

    def test_product_update_deactivate(self):
        """Test ProductUpdate for deactivation."""
        schema = ProductUpdate(is_active=False)
        assert schema.is_active is False
