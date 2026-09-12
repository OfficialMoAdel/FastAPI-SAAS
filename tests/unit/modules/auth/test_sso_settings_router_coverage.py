"""Unit tests for SSO Settings router (modules/sso_settings/router.py)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.api.deps.request_context import RequestContext
from src.app.core.tenancy.context import TenantContext
from src.app.modules.auth.routers.sso_settings import (
    create_sso_settings,
    delete_sso_settings,
    get_sso_settings,
    list_available_providers,
    list_sso_settings,
    update_sso_settings,
)
from src.app.modules.auth.routers.sso_settings import (
    router as sso_router_inst,
)
from src.app.shared.exceptions import ModelNotFoundError, ValidationError


class _RouterShim:
    create_sso_settings = staticmethod(create_sso_settings)
    delete_sso_settings = staticmethod(delete_sso_settings)
    get_sso_settings = staticmethod(get_sso_settings)
    list_available_providers = staticmethod(list_available_providers)
    list_sso_settings = staticmethod(list_sso_settings)
    update_sso_settings = staticmethod(update_sso_settings)
    routes = sso_router_inst.routes


sso_router = _RouterShim()
from src.app.modules.auth.crud import crud_sso_settings


def _make_ctx(user_id=1):
    """Build a RequestContext for a superuser-style principal."""
    tenant = TenantContext(
        tenant_id=uuid4(),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )
    return RequestContext(user=MagicMock(id=user_id), tenant=tenant, db=AsyncMock())


@pytest.fixture
def ctx():
    return _make_ctx()


async def test_create_sso_settings_success(ctx):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)),
        patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc,
    ):
        svc.create_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.create_sso_settings(
            request=MagicMock(), settings=MagicMock(provider="google"), ctx=ctx
        )
    assert result["provider"] == "google"


async def test_create_sso_settings_duplicate(ctx):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)):
        with pytest.raises(ValidationError, match="already exist"):
            await sso_router.create_sso_settings(request=MagicMock(), settings=MagicMock(provider="google"), ctx=ctx)


async def test_list_sso_settings(ctx):
    with patch(
        "src.app.modules.auth.routers.sso_settings.fetch_page_with_total",
        AsyncMock(return_value={"data": [{"provider": "google"}], "total_count": 1}),
    ):
        result = await sso_router.list_sso_settings(request=MagicMock(), ctx=ctx, page=1, per_page=10)
    assert result["items"] == [{"provider": "google"}]
    assert result["total"] == 1


async def test_list_available_providers():
    result = await sso_router.list_available_providers()
    assert "google" in result


async def test_get_sso_settings_success(ctx):
    with patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc:
        svc.get_decrypted_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.get_sso_settings(request=MagicMock(), provider="google", ctx=ctx)
    assert result["provider"] == "google"


async def test_get_sso_settings_not_found(ctx):
    with patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc:
        svc.get_decrypted_settings = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError, match="not found"):
            await sso_router.get_sso_settings(request=MagicMock(), provider="google", ctx=ctx)


async def test_update_sso_settings_success(ctx):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)),
        patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc,
    ):
        svc.update_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.update_sso_settings(
            request=MagicMock(), provider="google", values=MagicMock(), ctx=ctx
        )
    assert result["provider"] == "google"


async def test_update_sso_settings_not_found(ctx):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)):
        with pytest.raises(ModelNotFoundError, match="not found"):
            await sso_router.update_sso_settings(request=MagicMock(), provider="google", values=MagicMock(), ctx=ctx)


async def test_delete_sso_settings_success(ctx):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)),
        patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc,
    ):
        svc.delete_settings = AsyncMock()
        result = await sso_router.delete_sso_settings(request=MagicMock(), provider="google", ctx=ctx)
    assert "deleted" in result["message"]


async def test_delete_sso_settings_not_found(ctx):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)):
        with pytest.raises(ModelNotFoundError, match="not found"):
            await sso_router.delete_sso_settings(request=MagicMock(), provider="google", ctx=ctx)


async def test_update_sso_settings_passes_ctx_user_id(ctx):
    """user_id passed to the service must come from the request context."""
    captured = {}

    async def fake_update_settings(db, provider, values, user_id=None):
        captured["user_id"] = user_id
        return {"provider": provider}

    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)),
        patch("src.app.modules.auth.services.sso_settings.sso_settings_service") as svc,
    ):
        svc.update_settings = fake_update_settings
        await sso_router.update_sso_settings(request=MagicMock(), provider="google", values=MagicMock(), ctx=ctx)
    assert captured["user_id"] == ctx.user_id
