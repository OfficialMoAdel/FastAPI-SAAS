import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import TenantContextError


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_rbac_exempt_path_tenant_from_path_params_uses_one_seam(mock_db):
    """A ``{tenant_id}`` path param resolves through the ONE seam — no Tenant query.

    Candidate 2: RBAC no longer imports the Tenant model or re-derives a
    tenant from the URL. The path value is handed to ``resolve_tenant_id``
    (ADR-0006) as an *explicit* value — normalized from the transport type
    (``str``) to the domain type (``UUID``) — so the check proceeds with no
    DB read at all.
    """
    from tests.helpers.auth import build_test_auth_context

    tenant_id = uuid.uuid4()
    checker = requires_permission(Permission.TENANT_UPDATE)

    # Mock request with path param
    request = MagicMock(spec=Request)
    request.path_params = {"tenant_id": str(tenant_id)}

    ctx = build_test_auth_context(
        {"id": 1, "is_superuser": False, "tenant_id": tenant_id},
        None,
    )

    with patch(
        "src.app.core.rbac.dependencies.has_permission_cached",
        return_value=True,
    ) as mock_perm:
        # Should execute successfully
        await checker(request=request, db=mock_db, auth_context=ctx)

    # The path tenant reached the permission lookup as a UUID...
    assert mock_perm.call_args[0][1] == tenant_id
    # ...and resolving it needed no Tenant SELECT (the model import is gone).
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rbac_exempt_path_enforces_tenant_isolation(mock_db):
    """Test requires_permission on exempt path blocks cross-tenant access."""
    from tests.helpers.auth import build_test_auth_context

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

    # R1-14: construct the real typed AuthContext (isinstance check).
    mock_auth_context = build_test_auth_context(
        {"id": 1, "is_superuser": False, "tenant_id": tenant_a_id},
        None,
    )

    # Should raise ForbiddenException due to tenant mismatch
    with patch(
        "src.app.core.rbac.dependencies.has_permission_cached",
        return_value=True,
    ):
        with pytest.raises(ForbiddenException) as exc_info:
            await checker(request=request, db=mock_db, auth_context=mock_auth_context)

        assert "User does not belong to the current tenant" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_rbac_exempt_path_uuid_string_coercion_match(mock_db):
    """Test that UUID/string tenant comparison mismatch is resolved via string conversion."""
    from tests.helpers.auth import build_test_auth_context

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
    ctx = build_test_auth_context(
        {"id": 1, "is_superuser": False, "tenant_id": tenant_id_uuid},
        None,
    )

    with patch(
        "src.app.core.rbac.dependencies.has_permission_cached",
        return_value=True,
    ):
        # Should execute successfully without ForbiddenException because string conversion matches them
        await checker(request=request, db=mock_db, auth_context=ctx)


@pytest.mark.asyncio
async def test_rbac_exempt_path_no_tenant_raises_forbidden(mock_db):
    """requires_permission answers a missing tenant context with 403, not 400.

    Candidate 2: the no-tenant refusal is now the one no-tenant contract
    (P3-#14) — ``TenantContextError``, the same class ``tenant_dependency``
    and ``RequestContext.require_tenant()`` raise — instead of the ad-hoc
    ``ValidationError`` (400) that made RBAC the odd one out. The message is
    unchanged.
    """
    from tests.helpers.auth import build_test_auth_context

    checker = requires_permission(Permission.TENANT_UPDATE)

    # Request has no tenant_id in path
    request = MagicMock(spec=Request)
    request.path_params = {}
    ctx = build_test_auth_context(
        {"id": 1, "is_superuser": False, "tenant_id": uuid.uuid4()},
        None,
    )

    with pytest.raises(TenantContextError) as exc_info:
        await checker(request=request, db=mock_db, auth_context=ctx)

    assert "Tenant context required for permission check" in str(exc_info.value)
