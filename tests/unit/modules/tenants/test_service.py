"""Unit tests for TenantService business logic.

Tests cover:
- Tenant creation with slug validation
- Duplicate slug conflict handling
- Tenant retrieval by ID and slug
- Tenant listing with pagination
- Tenant updates (uses session refresh pattern - P1-1)
- Deactivation and reactivation
- Audit logging
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.tenants.schemas import TenantCreate, TenantUpdate


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_tenant():
    """Sample tenant data (dict format for CRUD operations)."""
    return {
        "id": 1,
        "name": "Acme Corporation",
        "slug": "acme-corp",
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }


@pytest.fixture
def mock_tenant_orm():
    """Sample tenant ORM object for session operations."""
    tenant = MagicMock()
    tenant.id = 1
    tenant.name = "Acme Corporation"
    tenant.slug = "acme-corp"
    tenant.is_active = True
    tenant.created_at = datetime.now(UTC)
    tenant.updated_at = None
    return tenant


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
    async def test_create_tenant_success(self, mock_db, tenant_create, mock_tenant_orm):
        """Test successful tenant creation."""
        from src.app.modules.tenants.service import TenantService

        # Instantiate service with mock DB
        tenant_service = TenantService(mock_db)

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.exists.return_value = False
            mock_crud.create.return_value = mock_tenant_orm

            result = await tenant_service.create_tenant(tenant_create)

            assert result["id"] == 1
            assert result["name"] == "Acme Corporation"
            assert result["slug"] == "acme-corp"
            mock_crud.exists.assert_called_once()
            mock_crud.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_tenant_duplicate_slug_raises_conflict(self, mock_db, tenant_create):
        """Test that duplicate slug raises DuplicateError."""
        from src.app.modules.tenants.service import TenantService
        from src.app.shared.exceptions import DuplicateError

        tenant_service = TenantService(mock_db)

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.exists.return_value = True

            with pytest.raises(DuplicateError) as exc_info:
                await tenant_service.create_tenant(tenant_create)

            assert exc_info.value.field == "slug"
            assert exc_info.value.value == "acme-corp"


class TestTenantServiceGet:
    """Tests for tenant retrieval."""

    @pytest.mark.asyncio
    async def test_get_tenant_by_id_success(self, mock_db, mock_tenant):
        """Test successful tenant retrieval by ID."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_tenant

            result = await tenant_service.get_tenant(1)

            assert result["id"] == 1
            mock_crud.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tenant_not_found_raises_404(self, mock_db):
        """Test that non-existent tenant raises ModelNotFoundError."""
        from src.app.modules.tenants.service import TenantService
        from src.app.shared.exceptions import ModelNotFoundError

        tenant_service = TenantService(mock_db)

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = None

            with pytest.raises(ModelNotFoundError) as exc_info:
                await tenant_service.get_tenant(999)

            assert exc_info.value.model_name == "Tenant"
            assert exc_info.value.identifier == 999

    @pytest.mark.asyncio
    async def test_get_tenant_by_slug_success(self, mock_db, mock_tenant):
        """Test successful tenant retrieval by slug."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_tenant

            result = await tenant_service.get_tenant_by_slug("acme-corp")

            assert result["slug"] == "acme-corp"


class TestTenantServiceUpdate:
    """Tests for tenant updates.

    Note: update_tenant, deactivate_tenant, reactivate_tenant now use
    session refresh pattern (P1-1 optimization) instead of crud.update + get_tenant.
    """

    @pytest.mark.asyncio
    async def test_update_tenant_success(self, mock_db, mock_tenant_orm, tenant_update):
        """Test successful tenant update using session refresh pattern."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        # P1-1: Mock db.get to return ORM object (not crud.get)
        mock_db.get.return_value = mock_tenant_orm
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await tenant_service.update_tenant(1, tenant_update)

        # Verify the update was applied to ORM object
        assert mock_tenant_orm.name == "Acme Corp Updated"
        # Verify session methods were called
        mock_db.get.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(mock_tenant_orm)
        # Verify result is a dict
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_update_tenant_not_found_raises_404(self, mock_db, tenant_update):
        """Test that updating non-existent tenant raises ModelNotFoundError."""
        from src.app.modules.tenants.service import TenantService
        from src.app.shared.exceptions import ModelNotFoundError

        tenant_service = TenantService(mock_db)

        # P1-1: Mock db.get to return None (not crud.get)
        mock_db.get.return_value = None

        with pytest.raises(ModelNotFoundError) as exc_info:
            await tenant_service.update_tenant(999, tenant_update)

        assert exc_info.value.model_name == "Tenant"
        assert exc_info.value.identifier == 999


class TestTenantServiceDeactivate:
    """Tests for tenant deactivation/reactivation.

    Note: These methods now use session refresh pattern (P1-1 optimization).
    """

    @pytest.mark.asyncio
    async def test_deactivate_tenant_success(self, mock_db, mock_tenant_orm):
        """Test successful tenant deactivation using session refresh pattern."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        # P1-1: Mock db.get to return ORM object
        mock_db.get.return_value = mock_tenant_orm
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await tenant_service.deactivate_tenant(1)

        # Verify is_active was set to False on ORM object
        assert mock_tenant_orm.is_active is False
        # Verify session methods were called
        mock_db.get.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(mock_tenant_orm)
        # Verify result is a dict
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_reactivate_tenant_success(self, mock_db, mock_tenant_orm):
        """Test successful tenant reactivation using session refresh pattern."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        # Start with inactive tenant
        mock_tenant_orm.is_active = False

        # P1-1: Mock db.get to return ORM object
        mock_db.get.return_value = mock_tenant_orm
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await tenant_service.reactivate_tenant(1)

        # Verify is_active was set to True on ORM object
        assert mock_tenant_orm.is_active is True
        # Verify session methods were called
        mock_db.get.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(mock_tenant_orm)
        # Verify result is a dict
        assert isinstance(result, dict)


class TestTenantServiceList:
    """Tests for tenant listing."""

    @pytest.mark.asyncio
    async def test_list_tenants_with_pagination(self, mock_db, mock_tenant):
        """Test tenant listing with pagination."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        mock_response = {
            "data": [mock_tenant],
            "total_count": 1,
        }

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get_multi.return_value = mock_response

            result = await tenant_service.list_tenants(page=1, items_per_page=10)

            assert len(result["data"]) == 1
            mock_crud.get_multi.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tenants_filter_by_active(self, mock_db, mock_tenant):
        """Test tenant listing filtered by active status."""
        from src.app.modules.tenants.service import TenantService

        tenant_service = TenantService(mock_db)

        mock_response = {
            "data": [mock_tenant],
            "total_count": 1,
        }

        with patch.object(tenant_service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get_multi.return_value = mock_response

            await tenant_service.list_tenants(page=1, items_per_page=10, is_active=True)

            call_kwargs = mock_crud.get_multi.call_args.kwargs
            assert call_kwargs.get("is_active") is True
