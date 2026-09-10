"""Unit tests for RateLimitService absorbing Tier management and models."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.rate_limits.models import RateLimit, Tier
from src.app.modules.rate_limits.router import get_rate_limit_service
from src.app.modules.rate_limits.schemas import (
    RateLimitCreate,
    TierCreate,
    TierUpdate,
)
from src.app.modules.rate_limits.service import RateLimitService
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def rate_limit_service(mock_db):
    return RateLimitService(mock_db)


class TestRateLimitServiceTierOperations:
    """Tests for Tier operations directly absorbed into RateLimitService."""

    @pytest.mark.asyncio
    async def test_create_tier_success(self, rate_limit_service, mock_db):
        tier_in = TierCreate(name="starter")
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.exists = AsyncMock(return_value=False)
            mock_tier_crud.create = AsyncMock(return_value={"id": 10, "name": "starter"})

            result = await rate_limit_service.create_tier(tier_in)

            assert result == {"id": 10, "name": "starter"}
            mock_tier_crud.exists.assert_awaited_once_with(db=mock_db, name="starter")
            mock_tier_crud.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_tier_duplicate_name(self, rate_limit_service, mock_db):
        tier_in = TierCreate(name="starter")
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.exists = AsyncMock(return_value=True)

            with pytest.raises(DuplicateError):
                await rate_limit_service.create_tier(tier_in)

    @pytest.mark.asyncio
    async def test_get_tier_by_id_success(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 1, "name": "free"})

            result = await rate_limit_service.get_tier_by_id(1)
            assert result == {"id": 1, "name": "free"}

    @pytest.mark.asyncio
    async def test_get_tier_by_id_not_found(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await rate_limit_service.get_tier_by_id(999)

    @pytest.mark.asyncio
    async def test_get_tier_by_name_success(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 2, "name": "enterprise"})

            result = await rate_limit_service.get_tier("enterprise")
            assert result == {"id": 2, "name": "enterprise"}

    @pytest.mark.asyncio
    async def test_update_tier_success(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 1, "name": "pro"})
            mock_tier_crud.update = AsyncMock()

            result = await rate_limit_service.update_tier("pro", TierUpdate())
            assert result == {"message": "Tier updated"}

    @pytest.mark.asyncio
    async def test_update_tier_rejects_rename(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 1, "name": "pro"})

            with pytest.raises(DuplicateError, match="renaming tier"):
                await rate_limit_service.update_tier("pro", TierUpdate(name="super-pro"))

    @pytest.mark.asyncio
    async def test_delete_tier_in_use_raises_conflict(self, rate_limit_service, mock_db):
        """Deleting a tier in use raises the domain DuplicateError (Q-M1: 409 mapping lives in core/errors.py)."""
        with patch.object(rate_limit_service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 5, "name": "busy_tier"})

            mock_result = MagicMock()
            mock_result.scalar_one.return_value = 3  # 3 users reference this tier
            mock_db.execute = AsyncMock(return_value=mock_result)

            with pytest.raises(DuplicateError) as exc_info:
                await rate_limit_service.delete_tier("busy_tier")

            assert "still in use" in exc_info.value.message


class TestRateLimitDirectTierResolution:
    """Tests verifying RateLimitService uses internal tier methods without cross-module lookups."""

    @pytest.mark.asyncio
    async def test_create_rate_limit_internal_tier_lookup(self, rate_limit_service, mock_db):
        """create_rate_limit resolves tier directly via get_tier without TierService."""
        rl_create = RateLimitCreate(path="/api/v1/data", limit=100, period=60)

        with (
            patch.object(rate_limit_service, "get_tier", new_callable=AsyncMock) as mock_get_tier,
            patch.object(rate_limit_service, "crud") as mock_rl_crud,
            patch.object(rate_limit_service, "_invalidate_tier_rate_limits_cache", new_callable=AsyncMock),
        ):
            mock_get_tier.return_value = {"id": 4, "name": "premium"}
            mock_rl_crud.exists = AsyncMock(return_value=False)
            mock_rl_crud.create = AsyncMock(return_value={"id": 1, "name": "premium_api_v1_data_100_60"})

            result = await rate_limit_service.create_rate_limit("premium", rl_create)

            mock_get_tier.assert_awaited_once_with("premium")
            assert result["id"] == 1

    def test_models_co_location(self):
        """Tier and RateLimit models live together in rate_limits.models."""
        assert Tier.__tablename__ == "tier"
        assert RateLimit.__tablename__ == "rate_limit"
        assert hasattr(Tier, "rate_limits")
        assert hasattr(RateLimit, "tier")

    def test_factory_dependency(self, mock_db):
        """get_rate_limit_service returns initialized instance."""
        svc = get_rate_limit_service(db=mock_db)
        assert isinstance(svc, RateLimitService)
        assert svc.db is mock_db

    def test_p0_1_crud_handles_decoupled(self, mock_db):
        """P0-1 regression: TierService and RateLimitService maintain distinct crud handles."""
        from src.app.modules.tiers.service import TierService

        rl_svc = RateLimitService(mock_db)
        t_svc = TierService(mock_db)

        assert rl_svc.crud.model is RateLimit
        assert rl_svc.tier_crud.model is Tier
        assert t_svc.crud.model is Tier
        assert t_svc.tier_crud.model is Tier
        assert t_svc.rate_limit_crud.model is RateLimit

    @pytest.mark.asyncio
    async def test_p1_3_delete_tier_fails_closed_on_db_error(self, rate_limit_service):
        """P1-3 regression: real DB query error during reference check raises RuntimeError."""
        from sqlalchemy.ext.asyncio import AsyncSession

        fake_session = MagicMock(spec=AsyncSession)
        fake_session.execute = AsyncMock(side_effect=ConnectionError("Database offline"))
        service = RateLimitService(fake_session)

        with patch.object(service, "tier_crud") as mock_tier_crud:
            mock_tier_crud.get = AsyncMock(return_value={"id": 1, "name": "doomed"})
            mock_tier_crud.delete = AsyncMock()

            with pytest.raises(RuntimeError, match="Failed to check referential integrity"):
                await service.delete_tier("doomed")

            mock_tier_crud.delete.assert_not_called()
