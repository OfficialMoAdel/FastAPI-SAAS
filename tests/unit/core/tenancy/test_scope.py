"""Phase 1: direct tests for the Tenant-scoped persistence seam.

Pins the new modules (``core/tenancy/scope.py``, ``core/db/scoped.py``):
reason-carrying scopes, bound CRUD, and the guarded + scoped executor.
Behavioral parity of the legacy delegates lives in test_tenant_seam.py.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.app.core.db.scoped import (
    _CRUD_CACHE,
    criterion,
    execute_scoped,
    scoped_crud,
)
from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    set_current_tenant,
)
from src.app.core.tenancy.scope import (
    ExplicitTenant,
    Unscoped,
    resolve_effective_tenant,
)
from src.app.modules.users.models import User
from src.app.shared.exceptions import TenantContextError


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid.uuid4(),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _clean_tenant_context():
    clear_tenant_context()
    yield
    clear_tenant_context()


class TestExplicitScope:
    def test_empty_reason_rejected(self) -> None:
        with pytest.raises(ValueError):
            ExplicitTenant(tenant_id=uuid.uuid4(), reason="")
        with pytest.raises(ValueError):
            Unscoped(reason="")

    def test_context_wins_over_explicit(self) -> None:
        ctx = _ctx()
        set_current_tenant(ctx)
        resolved = resolve_effective_tenant(
            ExplicitTenant(tenant_id=uuid.uuid4(), reason="worker"),
            model_name="User",
        )
        assert resolved == ctx.tenant_id

    def test_explicit_honored_without_context(self) -> None:
        tid = uuid.uuid4()
        resolved = resolve_effective_tenant(
            ExplicitTenant(tenant_id=tid, reason="worker"), model_name="User"
        )
        assert resolved == tid

    def test_fail_closed_without_scope(self) -> None:
        with pytest.raises(TenantContextError):
            resolve_effective_tenant(None, model_name="User")

    def test_unscoped_returns_none_without_context(self) -> None:
        assert (
            resolve_effective_tenant(Unscoped(reason="cron"), model_name="User")
            is None
        )

    def test_unscoped_ignored_with_context(self) -> None:
        ctx = _ctx()
        set_current_tenant(ctx)
        assert (
            resolve_effective_tenant(Unscoped(reason="cron"), model_name="User")
            == ctx.tenant_id
        )


class TestScopedCrud:
    def test_binds_scope(self) -> None:
        scope = ExplicitTenant(tenant_id=uuid.uuid4(), reason="worker")
        crud = scoped_crud(AsyncMock(), User, scope=scope)
        assert crud._bound_scope is scope

    def test_bound_scope_enforced_without_context(self) -> None:
        tid = uuid.uuid4()
        crud = scoped_crud(
            AsyncMock(), User, scope=ExplicitTenant(tenant_id=tid, reason="w")
        )
        result = crud.inject_tenant_filter({})
        assert result["tenant_id"] == tid

    def test_unbound_tenant_model_returns_cached_tenant_crud(self) -> None:
        _CRUD_CACHE.clear()
        try:
            first = scoped_crud(AsyncMock(), User)
            assert isinstance(first, TenantAwareCRUD)
            assert scoped_crud(AsyncMock(), User) is first
            assert _CRUD_CACHE[User] is first
        finally:
            _CRUD_CACHE.clear()

    def test_unbound_global_model_returns_plain_fastcrud(self) -> None:
        from fastcrud import FastCRUD

        from src.app.modules.tiers.models import Tier

        _CRUD_CACHE.clear()
        try:
            crud = scoped_crud(AsyncMock(), Tier)
            assert type(crud) is FastCRUD
            assert scoped_crud(AsyncMock(), Tier) is crud
        finally:
            _CRUD_CACHE.clear()

    def test_bound_scope_is_never_cached(self) -> None:
        tid = uuid.uuid4()
        one = scoped_crud(
            AsyncMock(), User, scope=ExplicitTenant(tenant_id=tid, reason="w1")
        )
        two = scoped_crud(
            AsyncMock(), User, scope=ExplicitTenant(tenant_id=tid, reason="w2")
        )
        assert one is not two


class TestExecuteScoped:
    async def test_fail_closed_without_context(self) -> None:
        db = AsyncMock()
        with pytest.raises(TenantContextError):
            await execute_scoped(select(User), db=db, model=User)
        db.execute.assert_not_awaited()

    async def test_scopes_and_guards_with_context(self) -> None:
        ctx = _ctx()
        set_current_tenant(ctx)
        db = AsyncMock()
        await execute_scoped(select(User), db=db, model=User)
        db.execute.assert_awaited_once()
        stmt = db.execute.await_args.args[0]
        assert "tenant_id" in str(stmt)

    async def test_explicit_scope_without_context(self) -> None:
        tid = uuid.uuid4()
        db = AsyncMock()
        await execute_scoped(
            select(User),
            db=db,
            model=User,
            scope=ExplicitTenant(tenant_id=tid, reason="worker"),
        )
        stmt = db.execute.await_args.args[0]
        # Literal UUIDs render dashless in SQL — compare dash-stripped.
        assert str(tid).replace("-", "") in str(
            stmt.compile(compile_kwargs={"literal_binds": True})
        ).replace("-", "")


class TestCriterion:
    def test_matches_tenant_scope_rule(self) -> None:
        ctx = _ctx()
        set_current_tenant(ctx)
        crit = criterion(User)
        assert crit.right.value == ctx.tenant_id

    def test_global_model_without_column_warns_not_raises(self) -> None:
        """Models declaring no tenant_id (Tier, Tenant, ...) are inherently
        global: the seam warns and applies a tautology instead of refusing
        system reads or raising AttributeError."""

        class GlobalSettings:
            pass

        crit = criterion(
            GlobalSettings,
            scope=ExplicitTenant(tenant_id=uuid.uuid4(), reason="test"),
        )
        assert str(crit) == "true"
