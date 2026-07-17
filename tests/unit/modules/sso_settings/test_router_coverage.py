"""Unit tests for SSO Settings router (modules/sso_settings/router.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.modules.sso_settings import router as sso_router
from src.app.modules.sso_settings.crud import crud_sso_settings


@pytest.fixture
def superuser():
    return {"id": 1}


async def test_create_sso_settings_success(superuser):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)),
        patch("src.app.modules.sso_settings.service.sso_settings_service") as svc,
    ):
        svc.create_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.create_sso_settings(
            request=MagicMock(), settings=MagicMock(provider="google"), current_superuser=superuser, db=AsyncMock()
        )
    assert result["provider"] == "google"


async def test_create_sso_settings_duplicate(superuser):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as exc:
            await sso_router.create_sso_settings(
                request=MagicMock(), settings=MagicMock(provider="google"), current_superuser=superuser, db=AsyncMock()
            )
    assert exc.value.status_code == 400


async def test_list_sso_settings(superuser):
    with patch.object(
        crud_sso_settings, "get_multi", AsyncMock(return_value={"data": [{"provider": "google"}], "total_count": 1})
    ):
        result = await sso_router.list_sso_settings(request=MagicMock(), db=AsyncMock(), page=1, per_page=10)
    assert result["items"] == [{"provider": "google"}]
    assert result["total"] == 1


async def test_list_available_providers():
    result = await sso_router.list_available_providers()
    assert "google" in result


async def test_get_sso_settings_success(superuser):
    with patch("src.app.modules.sso_settings.service.sso_settings_service") as svc:
        svc.get_decrypted_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.get_sso_settings(request=MagicMock(), provider="google", db=AsyncMock())
    assert result["provider"] == "google"


async def test_get_sso_settings_not_found(superuser):
    with patch("src.app.modules.sso_settings.service.sso_settings_service") as svc:
        svc.get_decrypted_settings = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await sso_router.get_sso_settings(request=MagicMock(), provider="google", db=AsyncMock())
    assert exc.value.status_code == 404


async def test_update_sso_settings_success(superuser):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)),
        patch("src.app.modules.sso_settings.service.sso_settings_service") as svc,
    ):
        svc.update_settings = AsyncMock(return_value={"provider": "google"})
        result = await sso_router.update_sso_settings(
            request=MagicMock(), provider="google", values=MagicMock(), current_superuser=superuser, db=AsyncMock()
        )
    assert result["provider"] == "google"


async def test_update_sso_settings_not_found(superuser):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await sso_router.update_sso_settings(
                request=MagicMock(), provider="google", values=MagicMock(), current_superuser=superuser, db=AsyncMock()
            )
    assert exc.value.status_code == 404


async def test_delete_sso_settings_success(superuser):
    with (
        patch.object(crud_sso_settings, "exists", AsyncMock(return_value=True)),
        patch("src.app.modules.sso_settings.service.sso_settings_service") as svc,
    ):
        svc.delete_settings = AsyncMock()
        result = await sso_router.delete_sso_settings(
            request=MagicMock(), provider="google", current_superuser=superuser, db=AsyncMock()
        )
    assert "deleted" in result["message"]


async def test_delete_sso_settings_not_found(superuser):
    with patch.object(crud_sso_settings, "exists", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await sso_router.delete_sso_settings(
                request=MagicMock(), provider="google", current_superuser=superuser, db=AsyncMock()
            )
    assert exc.value.status_code == 404
