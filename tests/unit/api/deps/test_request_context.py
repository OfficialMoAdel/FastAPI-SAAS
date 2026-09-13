"""Unit tests for the RequestContext dependency (#1 architecture deepening)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import AuthenticatedUser, get_current_user
from src.app.api.deps.request_context import (
    RequestContext,
    get_request_context,
)
from src.app.api.deps.tenant import tenant_dependency
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, TenantContextError
from src.app.core.tenancy.context import NO_TENANT, TenantContext


def _make_user(**overrides: Any) -> AuthenticatedUser:
    defaults: dict[str, Any] = {
        "id": 42,
        "email": "user@example.com",
        "is_superuser": False,
    }
    defaults.update(overrides)
    return AuthenticatedUser(**defaults)


def _make_tenant(tenant_id: str = "11111111-1111-1111-1111-111111111111") -> TenantContext:
    return TenantContext(
        tenant_id=__import__("uuid").UUID(tenant_id),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


class TestRequestContext:
    def test_user_id_property(self) -> None:
        ctx = RequestContext(user=_make_user(id=7), tenant=_make_tenant(), db=MagicMock())
        assert ctx.user_id == 7

    def test_is_superuser(self) -> None:
        ctx = RequestContext(user=_make_user(is_superuser=True), tenant=_make_tenant(), db=MagicMock())
        assert ctx.is_superuser is True

    def test_has_tenant_true(self) -> None:
        ctx = RequestContext(user=_make_user(), tenant=_make_tenant(), db=MagicMock())
        assert ctx.has_tenant is True

    def test_has_tenant_false_for_sentinel(self) -> None:
        ctx = RequestContext(user=_make_user(), tenant=NO_TENANT, db=MagicMock())
        assert ctx.has_tenant is False

    def test_require_tenant_raises_without_tenant(self) -> None:
        # P3-#14: one no-tenant contract — TenantContextError, not ForbiddenException.
        ctx = RequestContext(user=_make_user(), tenant=NO_TENANT, db=MagicMock())
        with pytest.raises(TenantContextError):
            ctx.require_tenant()

    def test_owns(self) -> None:
        ctx = RequestContext(user=_make_user(id=7), tenant=_make_tenant(), db=MagicMock())
        assert ctx.owns(7) is True
        assert ctx.owns(8) is False

    def test_require_ownership_passes_for_owner(self) -> None:
        ctx = RequestContext(user=_make_user(id=7), tenant=_make_tenant(), db=MagicMock())
        ctx.require_ownership(7)  # no exception

    def test_require_ownership_raises_for_other(self) -> None:
        ctx = RequestContext(user=_make_user(id=7), tenant=_make_tenant(), db=MagicMock())
        with pytest.raises(ForbiddenException):
            ctx.require_ownership(8)

    def test_require_ownership_allows_superuser(self) -> None:
        ctx = RequestContext(user=_make_user(id=7, is_superuser=True), tenant=_make_tenant(), db=MagicMock())
        ctx.require_ownership(999)  # no exception

    def test_frozen(self) -> None:
        ctx = RequestContext(user=_make_user(), tenant=_make_tenant(), db=MagicMock())
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            ctx.user = _make_user(id=2)  # type: ignore[misc]


class TestRequestContextDependency:
    def _build_app_client(self, user: AuthenticatedUser, tenant: TenantContext) -> TestClient:
        app = FastAPI()

        async def override_user() -> AuthenticatedUser:
            return user

        def override_tenant() -> TenantContext:
            return tenant

        async def override_db() -> AsyncMock:
            return AsyncMock(spec=AsyncSession)

        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[tenant_dependency] = override_tenant
        app.dependency_overrides[async_get_db] = override_db

        @app.get("/probe")
        async def probe(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
            return {"user_id": ctx.user_id, "tenant_slug": ctx.tenant.slug}

        return TestClient(app)

    def test_dependency_composes_context(self) -> None:
        client = self._build_app_client(_make_user(id=5), _make_tenant())
        resp = client.get("/probe")
        assert resp.status_code == 200
        assert resp.json() == {"user_id": 5, "tenant_slug": "acme"}

    def test_auth_failure_propagates(self) -> None:
        # No overrides for auth -> oauth2 scheme fails -> 401 from scheme.
        app = FastAPI()

        @app.get("/probe")
        async def probe(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:  # pragma: no cover
            return {}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe")
        assert resp.status_code == 401
