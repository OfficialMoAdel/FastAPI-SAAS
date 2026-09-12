"""Unit tests for tiers/service.py.

P1-18: TierService is the canonical home of tier business logic
(validation, duplicate guards, not-found mapping) with its own
tier_crud — these tests exercise that single implementation directly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.schemas import MessageResponse
from src.app.modules.tiers.router import get_tier_service
from src.app.modules.tiers.schemas import TierRead
from src.app.modules.tiers.service import TierService
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError


def _tier_read(tier_id: int = 1, name: str = "pro") -> TierRead:
    return TierRead(id=tier_id, name=name, created_at=datetime(2025, 1, 1, tzinfo=UTC))


def _make_service():
    db = MagicMock()
    return TierService(db), db


async def test_create_tier_success():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "tier_crud") as crud:
        crud.exists = AsyncMock(return_value=False)
        crud.create = AsyncMock(return_value={"id": 1, "name": "pro"})
        result = await service.create_tier(tier)
    assert result == {"id": 1, "name": "pro"}


async def test_create_tier_duplicate():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "tier_crud") as crud:
        crud.exists = AsyncMock(return_value=True)
        with pytest.raises(DuplicateError):
            await service.create_tier(tier)


async def test_create_tier_not_created():
    service, db = _make_service()
    tier = MagicMock()
    tier.model_dump.return_value = {"name": "pro"}
    with patch.object(service, "tier_crud") as crud:
        crud.exists = AsyncMock(return_value=False)
        crud.create = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.create_tier(tier)


async def test_get_tiers():
    service, db = _make_service()
    with (
        patch.object(service, "tier_crud") as crud,
        patch("src.app.modules.tiers.service.paginated_response") as paginated,
    ):
        crud.get_multi = AsyncMock(return_value={"data": []})
        paginated.return_value = {"items": []}
        result = await service.get_tiers(page=2, items_per_page=5)
    crud.get_multi.assert_awaited_once()
    paginated.assert_called_once()
    assert result == {"items": []}


async def test_get_tier_by_id_success():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value={"id": 1})
        result = await service.get_tier_by_id(1)
    assert result == {"id": 1}


async def test_get_tier_by_id_not_found():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.get_tier_by_id(1)


async def test_get_tier_by_name_success():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value={"id": 1, "name": "pro"})
        result = await service.get_tier("pro")
    assert result == {"id": 1, "name": "pro"}


async def test_get_tier_by_name_not_found():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.get_tier("pro")


async def test_update_tier_success():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value={"name": "pro"})
        crud.update = AsyncMock()
        result = await service.update_tier("pro", MagicMock())
    assert result == MessageResponse(message="Tier updated")


async def test_update_tier_not_found():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.update_tier("pro", MagicMock())


async def test_delete_tier_success():
    service, db = _make_service()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))
    with (
        patch.object(service, "tier_crud") as crud,
        patch("src.app.modules.tiers.service.get_redis", return_value=None),
    ):
        crud.get = AsyncMock(return_value=_tier_read(1, "pro"))
        crud.delete = AsyncMock()
        result = await service.delete_tier("pro")
    assert result == MessageResponse(message="Tier deleted")


async def test_delete_tier_not_found():
    service, db = _make_service()
    with patch.object(service, "tier_crud") as crud:
        crud.get = AsyncMock(return_value=None)
        with pytest.raises(ModelNotFoundError):
            await service.delete_tier("pro")


def test_get_tier_service_factory():
    db = MagicMock()
    result = get_tier_service(db=db)
    assert isinstance(result, TierService)


# P1-18: single ownership — TierService owns tier logic outright (no
# composed RateLimitService, no delegation). Its crud handles stay
# tier-primary (P0-1 decoupling contract).
def test_tier_service_single_ownership():
    svc, _ = _make_service()
    assert not hasattr(svc, "_rate_limits")
    assert svc.crud.model is svc.tier_crud.model
    from src.app.modules.rate_limits.models import RateLimit
    from src.app.modules.tiers.models import Tier

    assert svc.tier_crud.model is Tier
    assert svc.rate_limit_crud.model is RateLimit


async def test_delete_tier_fails_closed_on_db_error():
    """P1-3 regression: real DB query error during reference check raises RuntimeError (never deletes)."""
    from sqlalchemy.ext.asyncio import AsyncSession

    fake_session = MagicMock(spec=AsyncSession)
    fake_session.execute = AsyncMock(side_effect=ConnectionError("Database offline"))
    service = TierService(fake_session)

    with patch.object(service, "tier_crud") as mock_tier_crud:
        mock_tier_crud.get = AsyncMock(return_value=_tier_read(1, "doomed"))
        mock_tier_crud.delete = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to check referential integrity"):
            await service.delete_tier("doomed")

        mock_tier_crud.delete.assert_not_called()


# B12: the warm_tiers cache warmer was removed - it wrote `tier:{name}` keys
# that no code path ever read and nothing invalidated them on mutation.


async def test_tier_service_factory_returns_instance():
    """get_tier_service constructs TierService around the given session."""
    db = AsyncMock()
    svc = get_tier_service(db=db)
    assert isinstance(svc, TierService)
    assert svc.db is db
