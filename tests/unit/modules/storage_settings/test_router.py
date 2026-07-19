"""Unit tests for storage_settings/router.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from src.app.modules.storage_settings import router as ss_router
from src.app.modules.storage_settings.service import (
    InvalidCredentialsError,
    StorageNotConfiguredError,
)


def _make_tenant():
    tenant = MagicMock()
    tenant.id = "tenant-1"
    return tenant


def _make_service():
    service = MagicMock()
    service.get_settings = AsyncMock()
    service.create_settings = AsyncMock()
    service.update_settings = AsyncMock()
    service.to_read_schema = MagicMock(return_value=MagicMock())
    return service


async def test_get_storage_settings_success():
    tenant = _make_tenant()
    service = _make_service()
    settings = MagicMock()
    service.get_settings.return_value = settings
    result = await ss_router.get_storage_settings(tenant=tenant, service=service)
    service.get_settings.assert_awaited_once_with("tenant-1")
    assert result is service.to_read_schema.return_value


async def test_get_storage_settings_not_found():
    tenant = _make_tenant()
    service = _make_service()
    service.get_settings.return_value = None
    with pytest.raises(HTTPException) as exc:
        await ss_router.get_storage_settings(tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


async def test_create_storage_settings_success():
    tenant = _make_tenant()
    service = _make_service()
    service.get_settings.return_value = None
    data = MagicMock()
    result = await ss_router.create_storage_settings(data=data, tenant=tenant, service=service)
    service.create_settings.assert_awaited_once_with("tenant-1", data)
    assert result is service.to_read_schema.return_value


async def test_create_storage_settings_conflict():
    tenant = _make_tenant()
    service = _make_service()
    service.get_settings.return_value = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await ss_router.create_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_409_CONFLICT


async def test_create_storage_settings_invalid_credentials():
    tenant = _make_tenant()
    service = _make_service()
    service.get_settings.return_value = None
    service.create_settings.side_effect = InvalidCredentialsError("bad creds")
    with pytest.raises(HTTPException) as exc:
        await ss_router.create_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


async def test_create_storage_settings_value_error():
    tenant = _make_tenant()
    service = _make_service()
    service.get_settings.return_value = None
    service.create_settings.side_effect = ValueError("invalid")
    with pytest.raises(HTTPException) as exc:
        await ss_router.create_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


async def test_update_storage_settings_success():
    tenant = _make_tenant()
    service = _make_service()
    data = MagicMock()
    result = await ss_router.update_storage_settings(data=data, tenant=tenant, service=service)
    service.update_settings.assert_awaited_once_with("tenant-1", data)
    assert result is service.to_read_schema.return_value


async def test_update_storage_settings_not_configured():
    tenant = _make_tenant()
    service = _make_service()
    service.update_settings.side_effect = StorageNotConfiguredError()
    with pytest.raises(HTTPException) as exc:
        await ss_router.update_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


async def test_update_storage_settings_invalid_credentials():
    tenant = _make_tenant()
    service = _make_service()
    service.update_settings.side_effect = InvalidCredentialsError("bad")
    with pytest.raises(HTTPException) as exc:
        await ss_router.update_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


async def test_update_storage_settings_value_error():
    tenant = _make_tenant()
    service = _make_service()
    service.update_settings.side_effect = ValueError("invalid")
    with pytest.raises(HTTPException) as exc:
        await ss_router.update_storage_settings(data=MagicMock(), tenant=tenant, service=service)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
