"""P3-#14: one no-tenant contract — tenant_dependency speaks TenantContextError.

``tenant_dependency`` delegates to ``require_active_tenant`` (the ONE
fail-closed accessor in ``core/tenancy/context.py``), so the dep layer,
the service layer, and ``RequestContext.require_tenant`` all raise the
same table-registered 403 for the same condition — never a raw 500.
"""

from uuid import UUID

import pytest

from src.app.api.deps.tenant import tenant_dependency
from src.app.core.tenancy.context import (
    NO_TENANT,
    TenantContext,
    clear_tenant_context,
    set_current_tenant,
)
from src.app.shared.exceptions import TenantContextError


def _active_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _clean_tenant_context():
    clear_tenant_context()
    yield
    clear_tenant_context()


class TestTenantDependencyContract:
    def test_returns_active_tenant(self) -> None:
        ctx = _active_ctx()
        set_current_tenant(ctx)
        assert tenant_dependency() is ctx

    def test_missing_tenant_raises_tenant_context_error(self) -> None:
        with pytest.raises(TenantContextError):
            tenant_dependency()

    def test_sentinel_passes_through_for_exempt_paths(self) -> None:
        # NO_TENANT is set deliberately by the middleware on exempt paths
        # (e.g. POST /api/v1/tenants bootstrapping). The dep layer admits
        # it; endpoints needing a real tenant opt into require_tenant() /
        # require_active_tenant(), which reject the sentinel.
        set_current_tenant(NO_TENANT)
        assert tenant_dependency() is NO_TENANT

    def test_inactive_tenant_passes_through(self) -> None:
        # The active gate lives in the middleware (400 on non-exempt paths)
        # and require_active_tenant (services) — not in this dependency,
        # so exempt-path flows keep their historical semantics.
        ctx = _active_ctx()
        inactive = TenantContext(
            tenant_id=ctx.tenant_id,
            tenant_slug=ctx.tenant_slug,
            tenant_name=ctx.tenant_name,
            is_active=False,
        )
        set_current_tenant(inactive)
        assert tenant_dependency() is inactive
