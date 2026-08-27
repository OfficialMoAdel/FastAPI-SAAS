"""Unit tests for tiers/service.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules.tiers import service as tiers_service
from src.app.modules.tiers.service import TierService, get_tier_service


def _make_service():
    db = MagicMock()
    return TierService(db), db


async def test_create_tier_success():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "crud") as crud:
        crud.exists = AsyncMock(return_value=False)
        crud.create = AsyncMock(return_value={"id": 1, "name": "pro"})
        result = await service.create_tier(tier)
    assert result == {"id": 1, "name": "pro"}


async def test_create_tier_duplicate():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "crud") as crud:
        crud.exists = AsyncMock(return_value=True)
        with pytest.raises(DuplicateError):
            await service.create_tier(tier)


async def test_create_tier_not_created():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "crud") as crud:
        crud.exists = AsyncMock(return_value=False)
        crud.create = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.create_tier(tier)


async def test_get_tiers():
    service, db = _make_service()
    with (
        patch.object(service, "crud") as crud,
        patch.object(tiers_service, "paginated_response", return_value={"items": []}) as pr,
    ):
        crud.get_multi = AsyncMock(return_value={"data": []})
        result = await service.get_tiers(page=2, items_per_page=5)
    crud.get_multi.assert_awaited_once()
    pr.assert_called_once()
    assert result == {"items": []}


async def test_get_tier_by_id_success():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value={"id": 1})
        result = await service.get_tier_by_id(1)
    assert result == {"id": 1}


async def test_get_tier_by_id_not_found():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.get_tier_by_id(1)


async def test_get_tier_by_name_success():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value={"id": 1, "name": "pro"})
        result = await service.get_tier("pro")
    assert result == {"id": 1, "name": "pro"}


async def test_get_tier_by_name_not_found():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.get_tier("pro")


async def test_update_tier_success():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value={"name": "pro"})
        crud.update = AsyncMock()
        result = await service.update_tier("pro", MagicMock())
    assert result == {"message": "Tier updated"}


async def test_update_tier_not_found():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.update_tier("pro", MagicMock())


async def test_delete_tier_success():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value={"name": "pro"})
        crud.delete = AsyncMock()
        result = await service.delete_tier("pro")
    assert result == {"message": "Tier deleted"}


async def test_delete_tier_not_found():
    service, db = _make_service()
    with patch.object(service, "crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.delete_tier("pro")


async def test_get_tier_service_factory():
    db = MagicMock()
    result = get_tier_service(db=db)
    assert isinstance(result, TierService)


# B12: the warm_tiers cache warmer was removed - it wrote `tier:{name}` keys
# that no code path ever read and nothing invalidated them on mutation.


async def test_tier_service_factory_returns_instance():
    """get_tier_service constructs TierService around the given session."""
    db = AsyncMock()
    svc = get_tier_service(db=db)
    assert isinstance(svc, TierService)
    assert svc.db is db
