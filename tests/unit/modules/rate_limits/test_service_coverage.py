"""Unit tests for RateLimitService using mocked tier service and CRUD."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.utils import cache as cache_module
from src.app.modules.rate_limits.schemas import RateLimitCreate, RateLimitUpdate
from src.app.modules.rate_limits.service import RateLimitService
from src.app.modules.tiers.schemas import TierRead
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError


def _make_service() -> tuple[RateLimitService, AsyncMock]:
    db = AsyncMock()
    return RateLimitService(db), db


def _patch_tier():
    """Patch the tier resolution seam (Q-M6: single implementation — get_tier).

    The old helper patched the module-level TierService alias, which the
    inherited `_get_tier_or_404` constructed; composition removed that
    branch, so the seam is now the get_tier method itself.
    """
    return patch.object(
        RateLimitService,
        "get_tier",
        AsyncMock(
            return_value=TierRead(id=5, name="free", created_at=datetime(2025, 1, 1, tzinfo=UTC))
        ),
    )


def _patch_crud(service: RateLimitService, crud: AsyncMock):
    return patch.object(service, "crud", crud)


async def test_get_rate_limits_by_tier_id_cache_miss() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get_multi.return_value = {"data": [{"id": 1}]}
    with (
        patch.object(cache_module, "client", None),
        _patch_crud(service, crud),
    ):
        out = await service.get_rate_limits_by_tier_id(tier_id=5)
    assert out == {"data": [{"id": 1}]}
    crud.get_multi.assert_awaited_once()


async def test_get_rate_limits_by_tier_id_cache_hit() -> None:
    service, db = _make_service()
    client = AsyncMock()
    client.get.return_value = '{"data": [{"id": 9}]}'
    crud = AsyncMock()
    with (
        patch.object(cache_module, "client", client),
        _patch_crud(service, crud),
    ):
        out = await service.get_rate_limits_by_tier_id(tier_id=5)
    assert out == {"data": [{"id": 9}]}
    crud.get_multi.assert_not_awaited()


async def test_create_rate_limit_success() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.exists.return_value = False
    crud.create.return_value = {"id": 1, "name": "free:/a:10:minute"}
    data = RateLimitCreate(path="/a", limit=10, period=60)
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        out = await service.create_rate_limit("free", data)
    assert out["id"] == 1
    crud.create.assert_awaited_once()


async def test_create_rate_limit_duplicate_name() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.exists.return_value = True
    data = RateLimitCreate(path="/a", limit=10, period=60)
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        with pytest.raises(DuplicateError):
            await service.create_rate_limit("free", data)


async def test_create_rate_limit_duplicate_path() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.exists.side_effect = [False, True]
    data = RateLimitCreate(path="/a", limit=10, period=60)
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        with pytest.raises(DuplicateError):
            await service.create_rate_limit("free", data)


async def test_create_rate_limit_none_returned_raises() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.exists.return_value = False
    crud.create.return_value = None
    data = RateLimitCreate(path="/a", limit=10, period=60)
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        with pytest.raises(ModelNotFoundError):
            await service.create_rate_limit("free", data)


async def test_get_rate_limits_paginated() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get_multi.return_value = {"data": [], "total": 0}
    with (
        _patch_tier(),
        _patch_crud(service, crud),
    ):
        out = await service.get_rate_limits("free", page=2, items_per_page=5)
    assert "data" in out
    crud.get_multi.assert_awaited_once()


async def test_get_rate_limit_found() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = {"id": 3, "name": "r"}
    with (
        _patch_tier(),
        _patch_crud(service, crud),
    ):
        out = await service.get_rate_limit("free", 3)
    assert out["id"] == 3


async def test_get_rate_limit_not_found() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = None
    with (
        _patch_tier(),
        _patch_crud(service, crud),
    ):
        with pytest.raises(ModelNotFoundError):
            await service.get_rate_limit("free", 99)


async def test_update_rate_limit_success() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = {"id": 3, "name": "r"}
    crud.exists.return_value = False
    values = RateLimitUpdate(name="newname")
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        out = await service.update_rate_limit("free", 3, values)
    assert out["message"] == "Rate Limit updated"
    crud.update.assert_awaited_once()


async def test_update_rate_limit_duplicate_name() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = {"id": 3, "name": "old"}
    crud.exists.return_value = True
    values = RateLimitUpdate(name="taken")
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        with pytest.raises(DuplicateError):
            await service.update_rate_limit("free", 3, values)


async def test_update_rate_limit_not_found() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = None
    values = RateLimitUpdate(name="x")
    with (
        _patch_tier(),
        _patch_crud(service, crud),
    ):
        with pytest.raises(ModelNotFoundError):
            await service.update_rate_limit("free", 3, values)


async def test_delete_rate_limit_success() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = {"id": 3, "name": "r"}
    with (
        _patch_tier(),
        _patch_crud(service, crud),
        patch.object(cache_module, "client", None),
    ):
        out = await service.delete_rate_limit("free", 3)
    assert out["message"] == "Rate Limit deleted"
    crud.delete.assert_awaited_once()


async def test_delete_rate_limit_not_found() -> None:
    service, db = _make_service()
    crud = AsyncMock()
    crud.get.return_value = None
    with (
        _patch_tier(),
        _patch_crud(service, crud),
    ):
        with pytest.raises(ModelNotFoundError):
            await service.delete_rate_limit("free", 3)
