import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_rbac_exempt_path_extracts_tenant_from_path_params(mock_db):
    """Test requires_permission on exempt path extracts tenant from request.path_params and succeeds."""
    tenant_id = uuid.uuid4()
    checker = requires_permission(Permission.TENANT_UPDATE)

    # Mock request with path param
    request = MagicMock(spec=Request)
    request.path_params = {"tenant_id": str(tenant_id)}

    # Mock Tenant DB lookup
    from src.app.modules.tenants.models import Tenant

    mock_tenant_model = Tenant(name="Test Tenant", slug="test-tenant")
    mock_tenant_model.id = tenant_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_tenant_model
    mock_db.execute.return_value = mock_result

    with (
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token",
            return_value={"id": 1, "is_superuser": False, "tenant_id": tenant_id},
        ),
        patch(
            "src.app.core.tenancy.context.get_current_tenant", return_value=None
        ),  # Exempt path has no tenant context
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ),
    ):
        # Should execute successfully
        await checker(request=request, token="fake_token", db=mock_db)

        # Verify it queried the database using the tenant_id in path
        mock_db.execute.assert_called_once()
        stmt = mock_db.execute.call_args[0][0]
        assert "where tenants.id = " in str(stmt).lower() or "where :id_1 =" in str(stmt).lower()


@pytest.mark.asyncio
async def test_rbac_exempt_path_enforces_tenant_isolation(mock_db):
    """Test requires_permission on exempt path blocks cross-tenant access."""
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()
    checker = requires_permission(Permission.TENANT_UPDATE)

    # Request tries to access tenant_b
    request = MagicMock(spec=Request)
    request.path_params = {"tenant_id": str(tenant_b_id)}

    from src.app.modules.tenants.models import Tenant

    mock_tenant_model = Tenant(name="Test Tenant B", slug="test-tenant-b")
    mock_tenant_model.id = tenant_b_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_tenant_model
    mock_db.execute.return_value = mock_result

    with (
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token",
            return_value={"id": 1, "is_superuser": False, "tenant_id": tenant_a_id},  # User belongs to tenant_a
        ),
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ),
    ):

        class DummyUser:
            def __init__(self):
                self.id = 1
                self.is_superuser = False
                self.tenant_id = tenant_a_id

        class DummyAuthContext:
            def __init__(self):
                self.is_dependency_override = False
                self.tenant = None
                self.user = DummyUser()

        mock_auth_context = DummyAuthContext()

        # Should raise ForbiddenException due to tenant mismatch
        with pytest.raises(ForbiddenException) as exc_info:
            await checker(request=request, token="fake_token", db=mock_db, auth_context=mock_auth_context)

        assert "User does not belong to the current tenant" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_rbac_exempt_path_uuid_string_coercion_match(mock_db):
    """Test that UUID/string tenant comparison mismatch is resolved via string conversion."""
    tenant_id_uuid = uuid.uuid4()
    tenant_id_str = str(tenant_id_uuid)
    checker = requires_permission(Permission.TENANT_UPDATE)

    # Request path param contains string UUID
    request = MagicMock(spec=Request)
    request.path_params = {"tenant_id": tenant_id_str}

    from src.app.modules.tenants.models import Tenant

    mock_tenant_model = Tenant(name="Test Tenant", slug="test-tenant")
    mock_tenant_model.id = tenant_id_uuid

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_tenant_model
    mock_db.execute.return_value = mock_result

    with (
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token",
            return_value={"id": 1, "is_superuser": False, "tenant_id": tenant_id_uuid},  # User has UUID object
        ),
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            return_value=True,
        ),
    ):
        # Should execute successfully without ForbiddenException because string conversion matches them
        await checker(request=request, token="fake_token", db=mock_db)


@pytest.mark.asyncio
async def test_rbac_exempt_path_no_tenant_raises_bad_request(mock_db):
    """Test that requires_permission raises 400 Bad Request if tenant context is missing and not in path."""
    checker = requires_permission(Permission.TENANT_UPDATE)

    # Request has no tenant_id in path
    request = MagicMock(spec=Request)
    request.path_params = {}

    with (
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token",
            return_value={"id": 1, "is_superuser": False, "tenant_id": uuid.uuid4()},
        ),
        patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await checker(request=request, token="fake_token", db=mock_db)

        assert exc_info.value.status_code == 400
        assert "Tenant context required for permission check" in str(exc_info.value.detail)
