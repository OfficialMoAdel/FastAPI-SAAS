"""Unit tests for storage_settings/router.py."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.app.api.deps.request_context import RequestContext
from src.app.core.tenancy.context import TenantContext
from src.app.modules.storage.errors import (
    InvalidCredentialsError,
    StorageNotConfiguredError,
)
from src.app.modules.storage.routers.settings import (
    create_storage_settings,
    get_storage_settings,
    update_storage_settings,
)
from src.app.shared.exceptions import (
    DuplicateError,
    ValidationError,
)


class _RouterShim:
    get_storage_settings = staticmethod(get_storage_settings)
    create_storage_settings = staticmethod(create_storage_settings)
    update_storage_settings = staticmethod(update_storage_settings)


ss_router = _RouterShim()


def _make_ctx(tenant_id=None):
    tenant = TenantContext(
        tenant_id=tenant_id or UUID("00000000-0000-0000-0000-000000000001"),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )
    return RequestContext(user=MagicMock(id=1), tenant=tenant, db=MagicMock())


def _make_service():
    service = MagicMock()
    service.get_settings = AsyncMock()
    service.create_settings = AsyncMock()
    service.update_settings = AsyncMock()
    service.to_read_schema = MagicMock(return_value=MagicMock())
    return service


async def test_get_storage_settings_success():
    ctx = _make_ctx()
    service = _make_service()
    settings = MagicMock()
    service.get_settings.return_value = settings
    result = await ss_router.get_storage_settings(ctx=ctx, service=service)
    service.get_settings.assert_awaited_once_with(ctx.tenant_id)
    assert result is service.to_read_schema.return_value


async def test_get_storage_settings_not_found():
    ctx = _make_ctx()
    service = _make_service()
    service.get_settings.return_value = None
    with pytest.raises(StorageNotConfiguredError, match="not configured"):
        await ss_router.get_storage_settings(ctx=ctx, service=service)


async def test_create_storage_settings_success():
    ctx = _make_ctx()
    service = _make_service()
    service.get_settings.return_value = None
    data = MagicMock()
    result = await ss_router.create_storage_settings(data=data, ctx=ctx, service=service)
    service.create_settings.assert_awaited_once_with(ctx.tenant_id, data)
    assert result is service.to_read_schema.return_value


async def test_create_storage_settings_conflict():
    ctx = _make_ctx()
    service = _make_service()
    service.get_settings.return_value = MagicMock()
    with pytest.raises(DuplicateError, match="already exist"):
        await ss_router.create_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_create_storage_settings_invalid_credentials():
    ctx = _make_ctx()
    service = _make_service()
    service.get_settings.return_value = None
    service.create_settings.side_effect = InvalidCredentialsError("bad creds")
    with pytest.raises(InvalidCredentialsError, match="bad creds"):
        await ss_router.create_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_create_storage_settings_value_error():
    ctx = _make_ctx()
    service = _make_service()
    service.get_settings.return_value = None
    service.create_settings.side_effect = ValueError("invalid")
    with pytest.raises(ValidationError, match="Invalid storage settings payload"):
        await ss_router.create_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_update_storage_settings_success():
    ctx = _make_ctx()
    service = _make_service()
    data = MagicMock()
    result = await ss_router.update_storage_settings(data=data, ctx=ctx, service=service)
    service.update_settings.assert_awaited_once_with(ctx.tenant_id, data)
    assert result is service.to_read_schema.return_value


async def test_update_storage_settings_not_configured():
    ctx = _make_ctx()
    service = _make_service()
    service.update_settings.side_effect = StorageNotConfiguredError()
    with pytest.raises(StorageNotConfiguredError):
        await ss_router.update_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_update_storage_settings_invalid_credentials():
    ctx = _make_ctx()
    service = _make_service()
    service.update_settings.side_effect = InvalidCredentialsError("bad")
    with pytest.raises(InvalidCredentialsError, match="bad"):
        await ss_router.update_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_update_storage_settings_value_error():
    ctx = _make_ctx()
    service = _make_service()
    service.update_settings.side_effect = ValueError("invalid")
    with pytest.raises(ValidationError, match="Invalid storage settings payload"):
        await ss_router.update_storage_settings(data=MagicMock(), ctx=ctx, service=service)


async def test_get_storage_settings_requires_tenant():
    """A tenant-less request context must be rejected with 403."""
    from src.app.core.tenancy.context import NO_TENANT

    ctx = RequestContext(user=MagicMock(id=1), tenant=NO_TENANT, db=MagicMock())
    service = _make_service()
    from src.app.core.exceptions.http_exceptions import ForbiddenException

    with pytest.raises(ForbiddenException):
        await ss_router.get_storage_settings(ctx=ctx, service=service)
    service.get_settings.assert_not_awaited()
