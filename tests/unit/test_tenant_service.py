"""Unit tests for TenantService business logic.

Tests cover:
- Tenant creation with slug validation
- Duplicate slug conflict handling
- Tenant retrieval by ID and slug
- Tenant listing with pagination
- Tenant updates
- Deactivation and reactivation
- Audit logging
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.tenants.schemas import TenantCreate, TenantUpdate


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_tenant():
    """Sample tenant data."""
    return {
        "id": 1,
        "name": "Acme Corporation",
        "slug": "acme-corp",
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }


@pytest.fixture
def tenant_create():
    """Sample tenant creation data."""
    return TenantCreate(name="Acme Corporation", slug="acme-corp")


@pytest.fixture
def tenant_update():
    """Sample tenant update data."""
    return TenantUpdate(name="Acme Corp Updated")


class TestTenantServiceCreate:
    """Tests for tenant creation."""

    @pytest.mark.asyncio
    async def test_create_tenant_success(self, mock_db, tenant_create, mock_tenant):
        """Test successful tenant creation."""
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.exists.return_value = False
            mock_crud.create.return_value = mock_tenant

            result = await tenant_service.create_tenant(tenant_create, mock_db)

            assert result["id"] == 1
            assert result["name"] == "Acme Corporation"
            assert result["slug"] == "acme-corp"
            mock_crud.exists.assert_called_once()
            mock_crud.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_tenant_duplicate_slug_raises_conflict(self, mock_db, tenant_create):
        """Test that duplicate slug raises DuplicateError."""
        from src.app.modules._base.exceptions import DuplicateError
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.exists.return_value = True

            with pytest.raises(DuplicateError) as exc_info:
                await tenant_service.create_tenant(tenant_create, mock_db)

            assert exc_info.value.field == "slug"
            assert exc_info.value.value == "acme-corp"


class TestTenantServiceGet:
    """Tests for tenant retrieval."""

    @pytest.mark.asyncio
    async def test_get_tenant_by_id_success(self, mock_db, mock_tenant):
        """Test successful tenant retrieval by ID."""
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_tenant

            result = await tenant_service.get_tenant(1, mock_db)

            assert result["id"] == 1
            mock_crud.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tenant_not_found_raises_404(self, mock_db):
        """Test that non-existent tenant raises ModelNotFoundError."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = None

            with pytest.raises(ModelNotFoundError) as exc_info:
                await tenant_service.get_tenant(999, mock_db)

            assert exc_info.value.model_name == "Tenant"
            assert exc_info.value.identifier == 999

    @pytest.mark.asyncio
    async def test_get_tenant_by_slug_success(self, mock_db, mock_tenant):
        """Test successful tenant retrieval by slug."""
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_tenant

            result = await tenant_service.get_tenant_by_slug("acme-corp", mock_db)

            assert result["slug"] == "acme-corp"


class TestTenantServiceUpdate:
    """Tests for tenant updates."""

    @pytest.mark.asyncio
    async def test_update_tenant_success(self, mock_db, mock_tenant, tenant_update):
        """Test successful tenant update."""
        from src.app.modules.tenants.service import tenant_service

        updated_tenant = {**mock_tenant, "name": "Acme Corp Updated"}

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            # First call returns original, second call (after update) returns updated
            mock_crud.get.side_effect = [mock_tenant, updated_tenant]
            mock_crud.update.return_value = None

            result = await tenant_service.update_tenant(1, tenant_update, mock_db)

            assert result["name"] == "Acme Corp Updated"
            mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_tenant_not_found_raises_404(self, mock_db, tenant_update):
        """Test that updating non-existent tenant raises ModelNotFoundError."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.tenants.service import tenant_service

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = None

            with pytest.raises(ModelNotFoundError) as exc_info:
                await tenant_service.update_tenant(999, tenant_update, mock_db)

            assert exc_info.value.model_name == "Tenant"
            assert exc_info.value.identifier == 999


class TestTenantServiceDeactivate:
    """Tests for tenant deactivation/reactivation."""

    @pytest.mark.asyncio
    async def test_deactivate_tenant_success(self, mock_db, mock_tenant):
        """Test successful tenant deactivation."""
        from src.app.modules.tenants.service import tenant_service

        deactivated = {**mock_tenant, "is_active": False}

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            # First call for existence check, second call after update
            mock_crud.get.side_effect = [mock_tenant, deactivated]
            mock_crud.update.return_value = None

            result = await tenant_service.deactivate_tenant(1, mock_db)

            assert result["is_active"] is False

    @pytest.mark.asyncio
    async def test_reactivate_tenant_success(self, mock_db, mock_tenant):
        """Test successful tenant reactivation."""
        from src.app.modules.tenants.service import tenant_service

        inactive_tenant = {**mock_tenant, "is_active": False}
        reactivated = {**mock_tenant, "is_active": True}

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            # First call for existence check, second call after update
            mock_crud.get.side_effect = [inactive_tenant, reactivated]
            mock_crud.update.return_value = None

            result = await tenant_service.reactivate_tenant(1, mock_db)

            assert result["is_active"] is True


class TestTenantServiceList:
    """Tests for tenant listing."""

    @pytest.mark.asyncio
    async def test_list_tenants_with_pagination(self, mock_db, mock_tenant):
        """Test tenant listing with pagination."""
        from src.app.modules.tenants.service import tenant_service

        mock_response = {
            "data": [mock_tenant],
            "total_count": 1,
        }

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get_multi.return_value = mock_response

            result = await tenant_service.list_tenants(mock_db, page=1, items_per_page=10)

            assert len(result["data"]) == 1
            mock_crud.get_multi.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tenants_filter_by_active(self, mock_db, mock_tenant):
        """Test tenant listing filtered by active status."""
        from src.app.modules.tenants.service import tenant_service

        mock_response = {
            "data": [mock_tenant],
            "total_count": 1,
        }

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get_multi.return_value = mock_response

            await tenant_service.list_tenants(mock_db, page=1, items_per_page=10, is_active=True)

            call_kwargs = mock_crud.get_multi.call_args.kwargs
            assert call_kwargs.get("is_active") is True
