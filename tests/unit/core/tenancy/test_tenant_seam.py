"""R2-05: direct tests for the fail-closed tenant isolation seams.

``require_active_tenant`` and ``TenantAwareCRUD.inject_tenant_filter`` are the
two enforcement points that keep tenant data isolated. Before this file they
were only exercised through mocks in billing/service tests — the actual
fail-closed behavior (raise on missing/inactive context, reject the NO_TENANT
sentinel, block caller-supplied tenant_id smuggling) had no direct coverage.

These tests pin that behavior so a regression that silently widens access
(e.g. re-introducing a ``tenant is not None`` check that lets the falsy
NO_TENANT sentinel through) fails loudly.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastcrud import FastCRUD
from pydantic import BaseModel

from src.app.core.db.scoped import criterion
from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.context import (
    NO_TENANT,
    TenantContext,
    clear_tenant_context,
    get_current_tenant,
    require_active_tenant,
    set_current_tenant,
)
from src.app.core.tenancy.scope import ExplicitTenant, Unscoped
from src.app.modules.users.models import User
from src.app.shared.exceptions import TenantContextError


def _ctx(is_active: bool = True) -> TenantContext:
    return TenantContext(
        tenant_id=uuid.uuid4(),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=is_active,
    )


@pytest.fixture(autouse=True)
def _clean_tenant_context():
    """Ensure no tenant context leaks between tests."""
    clear_tenant_context()
    yield
    clear_tenant_context()


class TestRequireActiveTenant:
    def test_raises_when_no_context(self):
        with pytest.raises(TenantContextError):
            require_active_tenant()

    def test_raises_when_tenant_inactive(self):
        set_current_tenant(_ctx(is_active=False))
        with pytest.raises(TenantContextError):
            require_active_tenant()

    def test_raises_for_no_tenant_sentinel(self):
        """NO_TENANT is falsy and inactive — must not pass as 'active'."""
        set_current_tenant(NO_TENANT)
        with pytest.raises(TenantContextError):
            require_active_tenant()

    def test_returns_context_when_active(self):
        ctx = _ctx(is_active=True)
        set_current_tenant(ctx)
        assert require_active_tenant() is ctx


class TestInjectTenantFilter:
    @pytest.fixture
    def crud(self) -> TenantAwareCRUD:
        # FastCRUD introspects model.__table__ at init, so use the real
        # tenant-scoped User model — exactly what these seams protect.
        return TenantAwareCRUD(model=User)

    def test_injects_tenant_id_from_context(self, crud):
        ctx = _ctx()
        set_current_tenant(ctx)
        result = crud.inject_tenant_filter({"status": "active"})
        assert result["tenant_id"] == ctx.tenant_id
        assert result["status"] == "active"

    def test_blocks_caller_supplied_tenant_id_override(self, crud):
        """A caller cannot smuggle a different tenant_id past the context."""
        ctx = _ctx()
        set_current_tenant(ctx)
        other = uuid.uuid4()
        result = crud.inject_tenant_filter({"tenant_id": other})
        assert result["tenant_id"] == ctx.tenant_id

    def test_rejects_no_tenant_sentinel(self, crud):
        """B1 FIX: falsy NO_TENANT must not scope queries to UUID(int=0)."""
        set_current_tenant(NO_TENANT)
        with pytest.raises(TenantContextError):
            crud.inject_tenant_filter({})

    def test_rejects_missing_context(self, crud):
        with pytest.raises(TenantContextError):
            crud.inject_tenant_filter({})

    def test_missing_context_ignores_caller_tenant_id(self, crud):
        """B1/T10: without context, a caller tenant_id cannot smuggle through."""
        with pytest.raises(TenantContextError):
            crud.inject_tenant_filter({"tenant_id": uuid.uuid4()})

    def test_allow_unscoped_flag_permits_missing_context(self, crud):
        """The explicit escape hatch still works for genuinely global queries."""
        result = crud.inject_tenant_filter({"_allow_unscoped": True})
        assert "tenant_id" not in result
        assert "_allow_unscoped" not in result  # consumed, never reaches FastCRUD

    def test_allow_unscoped_consumed_even_with_context(self, crud):
        """B6-LEAK FIX: flag is popped unconditionally, never leaks to FastCRUD."""
        set_current_tenant(_ctx())
        result = crud.inject_tenant_filter({"_allow_unscoped": True, "x": 1})
        assert "_allow_unscoped" not in result
        assert result["x"] == 1

    def test_string_tenant_id_coerced_to_uuid(self, crud):
        from src.app.core.tenancy import context as ctx_mod

        ctx = _ctx()
        token = ctx_mod._current_tenant.set(
            TenantContext(
                tenant_id=str(ctx.tenant_id),  # type: ignore[arg-type]
                tenant_slug="acme",
                tenant_name="Acme",
                is_active=True,
            )
        )
        try:
            result = crud.inject_tenant_filter({})
            assert result["tenant_id"] == ctx.tenant_id
        finally:
            ctx_mod._current_tenant.reset(token)

    def test_invalid_string_tenant_id_raises(self, crud):
        from src.app.core.tenancy import context as ctx_mod

        token = ctx_mod._current_tenant.set(
            TenantContext(
                tenant_id="not-a-uuid",  # type: ignore[arg-type]
                tenant_slug="acme",
                tenant_name="Acme",
                is_active=True,
            )
        )
        try:
            with pytest.raises(ValueError):
                crud.inject_tenant_filter({})
        finally:
            ctx_mod._current_tenant.reset(token)



class _WritePayload(BaseModel):
    tenant_id: uuid.UUID | None = None
    value: str = "value"


class TestTenantAwareWrites:
    @pytest.mark.asyncio
    async def test_create_uses_explicit_scope_without_context(self, monkeypatch):
        tenant_id = uuid.uuid4()
        scoped_crud = TenantAwareCRUD(
            model=User,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="worker"),
        )
        create = AsyncMock(return_value="created")
        monkeypatch.setattr(FastCRUD, "create", create)
        payload = _WritePayload(tenant_id=uuid.uuid4())

        assert await scoped_crud.create(AsyncMock(), payload) == "created"
        assert payload.tenant_id == tenant_id
        create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_context_wins_over_bound_scope(self, monkeypatch):
        context = _ctx()
        set_current_tenant(context)
        scoped_crud = TenantAwareCRUD(
            model=User,
            scope=ExplicitTenant(tenant_id=uuid.uuid4(), reason="worker"),
        )
        create = AsyncMock(return_value="created")
        monkeypatch.setattr(FastCRUD, "create", create)
        payload = _WritePayload(tenant_id=uuid.uuid4())

        await scoped_crud.create(AsyncMock(), payload)

        assert payload.tenant_id == context.tenant_id
        create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_rejects_missing_context_and_scope(self, monkeypatch):
        create = AsyncMock()
        monkeypatch.setattr(FastCRUD, "create", create)
        crud = TenantAwareCRUD(model=User)

        with pytest.raises(TenantContextError, match="without active tenant context"):
            await crud.create(AsyncMock(), _WritePayload())
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_rejects_unscoped_bound_scope(self, monkeypatch):
        create = AsyncMock()
        monkeypatch.setattr(FastCRUD, "create", create)
        unscoped_crud = TenantAwareCRUD(model=User, scope=Unscoped(reason="system"))

        with pytest.raises(TenantContextError, match="cannot use an unscoped scope"):
            await unscoped_crud.create(AsyncMock(), _WritePayload())
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_uses_explicit_scope_and_rewrites_payload(self, monkeypatch):
        tenant_id = uuid.uuid4()
        scoped_crud = TenantAwareCRUD(
            model=User,
            scope=ExplicitTenant(tenant_id=tenant_id, reason="worker"),
        )
        update = AsyncMock()
        monkeypatch.setattr(FastCRUD, "update", update)
        payload = _WritePayload(tenant_id=uuid.uuid4())

        await scoped_crud.update(AsyncMock(), payload, id=1)

        assert payload.tenant_id == tenant_id
        update.assert_awaited_once()
        assert update.await_args.kwargs["tenant_id"] == tenant_id


class TestTenantScope:
    """Direct coverage for the criterion seam (architecture candidate 1).

    criterion() is the ONE tenant-filter spelling and must mirror
    inject_tenant_filter's security semantics: context
    wins, NO_TENANT rejected, missing context fail-closed unless the
    explicit escape hatch (which yields a None criterion = no filter) —
    plus the worker path: an explicit tenant_id argument is honored when
    no context is active.
    """

    def test_criterion_binds_context_tenant(self):
        ctx = _ctx()
        set_current_tenant(ctx)
        crit = criterion(User)
        compiled = str(crit)
        assert "tenant_id" in compiled

    def test_context_wins_over_caller_argument(self):
        """M-4: caller cannot smuggle a different tenant past the context."""
        ctx = _ctx()
        set_current_tenant(ctx)
        other = uuid.uuid4()
        crit = criterion(User, tenant_id=other)
        # RHS must be the context id, not `other`.
        assert crit.right.value == ctx.tenant_id

    def test_explicit_tenant_id_used_without_context(self):
        """Worker path: explicit argument is the scope when context is unset."""
        tid = uuid.uuid4()
        crit = criterion(User, tenant_id=tid)
        assert crit.right.value == tid

    def test_string_tenant_id_argument_passed_through(self):
        tid = uuid.uuid4()
        crit = criterion(User, tenant_id=str(tid))
        assert crit.right.value == str(tid)

    def test_rejects_no_context_no_argument(self):
        with pytest.raises(TenantContextError):
            criterion(User)

    def test_rejects_no_tenant_sentinel(self):
        set_current_tenant(NO_TENANT)
        with pytest.raises(TenantContextError):
            criterion(User)

    def test_allow_unscoped_returns_true_tautology(self):
        """Escape hatch: a true() tautology — no filter, safe to chain unconditionally."""
        from sqlalchemy import true

        crit = criterion(User, allow_unscoped=True)
        assert crit.compare(true())


    def test_none_tenant_id_argument_rejected(self):
        with pytest.raises(TenantContextError):
            criterion(User, tenant_id=None)  # no context either


class TestGetSetClearRoundTrip:
    def test_set_get_clear(self):
        assert get_current_tenant() is None
        ctx = _ctx()
        set_current_tenant(ctx)
        assert get_current_tenant() is ctx
        clear_tenant_context()
        assert get_current_tenant() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
