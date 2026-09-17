"""Unit tests for Candidate 11: Collapsing Tenant | TenantContext duality."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.db.rls import RLSAuditFailureError
from src.app.core.tenancy.context import TenantContext
from src.app.middleware.tenant import TenantMiddleware
from src.app.modules.tenants.models import Tenant


def test_tenant_context_properties_and_aliases():
    """Verify TenantContext exposes both id and tenant_id, slug and tenant_slug."""
    tid = uuid4()
    ctx = TenantContext(
        tenant_id=tid,
        tenant_slug="acme",
        tenant_name="Acme Corp",
        is_active=True,
    )

    # Both aliases must resolve identically
    assert ctx.tenant_id == tid
    assert ctx.id == tid
    assert ctx.tenant_slug == "acme"
    assert ctx.slug == "acme"
    assert ctx.tenant_name == "Acme Corp"
    assert ctx.name == "Acme Corp"
    assert ctx.is_active is True


def test_tenant_context_from_model():
    """Verify TenantContext.from_model correctly extracts all essential fields."""
    tid = uuid4()
    model = MagicMock(spec=Tenant)
    model.id = tid
    model.slug = "beta"
    model.name = "Beta Inc"
    model.is_active = True

    ctx = TenantContext.from_model(model)
    assert isinstance(ctx, TenantContext)
    assert ctx.id == tid
    assert ctx.slug == "beta"
    assert ctx.is_active is True


@pytest.mark.asyncio
async def test_get_tenant_by_slug_db_fetch_returns_tenant_context():
    """Verify DB fetch returns TenantContext (not raw ORM model), collapsing duality."""
    tid = uuid4()
    mock_tenant = MagicMock(spec=Tenant)
    mock_tenant.id = tid
    mock_tenant.slug = "gamma"
    mock_tenant.name = "Gamma LLC"
    mock_tenant.is_active = True

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_tenant
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.connection = AsyncMock(return_value=AsyncMock())

    middleware = TenantMiddleware(app=MagicMock())

    with (
        patch("src.app.middleware.tenant.local_session") as mock_local_session,
        patch("src.app.middleware.tenant.get_redis", return_value=None),
    ):
        mock_local_session.return_value.__aenter__.return_value = mock_session
        result = await middleware._get_tenant_by_slug("gamma")

    assert result is not None
    # Crucial: Must be TenantContext, NOT raw ORM model
    assert isinstance(result, TenantContext)
    assert result.id == tid
    assert result.slug == "gamma"


@pytest.mark.asyncio
async def test_get_tenant_by_slug_audit_failure_falls_back():
    """Q-M9: RLS bypass audit failure is caught by TYPE (not string-match) and
    tenant resolution falls back to the standard RLS-protected query."""
    tid = uuid4()
    mock_tenant = MagicMock(spec=Tenant)
    mock_tenant.id = tid
    mock_tenant.slug = "delta"
    mock_tenant.name = "Delta Inc"
    mock_tenant.is_active = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_tenant

    execute_results = iter(
        [
            RLSAuditFailureError("RLS bypass aborted due to audit log failure: boom"),
            mock_result,
        ]
    )

    async def execute_side_effect(*args, **kwargs):
        item = next(execute_results)
        if isinstance(item, Exception):
            raise item
        return item

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.connection = AsyncMock(return_value=AsyncMock())

    middleware = TenantMiddleware(app=MagicMock())

    with (
        patch("src.app.middleware.tenant.local_session") as mock_local_session,
        patch("src.app.middleware.tenant.get_redis", return_value=None),
        # A2: the L1/L2/DB read-through (incl. the Q-M9 fallback log) lives
        # in the canonical resolver; the middleware only forwards to it.
        patch("src.app.core.tenancy.resolver.logger") as mock_logger,
    ):
        mock_local_session.return_value.__aenter__.return_value = mock_session
        result = await middleware._get_tenant_by_slug("delta")

    assert result is not None
    assert isinstance(result, TenantContext)
    assert result.id == tid
    # The fallback was logged as an error
    assert mock_logger.error.called
