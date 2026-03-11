from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.app.modules.tenants.schemas import TenantCreate
from src.app.modules.tenants.service import TenantService


@pytest.fixture
def mock_db():
    return AsyncMock()


class TestTenantSlugNormalization:
    @pytest.mark.asyncio
    async def test_schema_normalization(self):
        """Test that TenantCreate schema normalizes slug."""
        # Case 1: Mixed case -> lowercase
        data = TenantCreate(name="Test", slug="MySlug")
        assert data.slug == "myslug"

        # Case 2: Whitespace -> stripped
        data = TenantCreate(name="Test", slug="  my-slug-2  ")
        assert data.slug == "my-slug-2"

    @pytest.mark.asyncio
    async def test_service_lookup_normalization(self, mock_db):
        """Test that get_tenant_by_slug normalizes input."""
        service = TenantService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = {"id": 1, "slug": "myslug"}

            # Call with mixed case
            await service.get_tenant_by_slug("MySlug")

            # Verify called with lowercase
            mock_crud.get.assert_called_with(db=mock_db, slug="myslug", schema_to_select=ANY)

            # Call with whitespace
            await service.get_tenant_by_slug("  MySlug  ")
            mock_crud.get.assert_called_with(db=mock_db, slug="myslug", schema_to_select=ANY)

    @pytest.mark.asyncio
    async def test_service_create_normalization(self, mock_db):
        """Test that create_tenant normalizes slug (defense in depth)."""
        service = TenantService(mock_db)

        # We construct TenantCreate with valid data first
        # We manually bypass validation to simulate a scenario where
        # bad data might reach the service if schema validation was bypassed or flawed,
        # ensuring service layer also normalizes.
        # However, since we fixed schema, we can just use schema.
        # But to test service logic explicitly, we can mock the object.

        tenant_data = TenantCreate(name="Test", slug="myslug")
        # Manually inject bad slug to verify service cleans it up
        tenant_data.slug = "MySlug"

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.exists.return_value = False
            # Return a mock Tenant object, not a dict
            mock_tenant = MagicMock()
            mock_tenant.id = 1
            mock_tenant.slug = "myslug"
            mock_tenant.name = "Test"
            mock_tenant.is_active = True
            mock_tenant.created_at = None
            mock_tenant.updated_at = None
            mock_crud.create.return_value = mock_tenant

            await service.create_tenant(tenant_data)

            # Verify passed object has normalized slug
            call_args = mock_crud.create.call_args
            passed_obj = call_args.kwargs["object"]
            assert passed_obj.slug == "myslug"
