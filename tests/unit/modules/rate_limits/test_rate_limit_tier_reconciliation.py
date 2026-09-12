"""Unit tests for RateLimitService composing TierService for tier operations (P1-18)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.schemas import MessageResponse
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.router import get_rate_limit_service
from src.app.modules.rate_limits.schemas import (
    RateLimitCreate,
    TierCreate,
    TierUpdate,
)
from src.app.modules.rate_limits.service import RateLimitService
from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import TierRead
from src.app.modules.tiers.service import TierService
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError


def _tier(tier_id: int, name: str) -> TierRead:
    return TierRead(id=tier_id, name=name, created_at=datetime(2025, 1, 1, tzinfo=UTC))


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def rate_limit_service(mock_db):
    return RateLimitService(mock_db)


class TestRateLimitServiceTierDelegation:
    """Tier operations on RateLimitService are deprecated shims delegating to the composed TierService."""

    @pytest.mark.asyncio
    async def test_create_tier_delegates(self, rate_limit_service, mock_db):
        tier_in = TierCreate(name="starter")
        created = _tier(10, "starter")
        with patch.object(rate_limit_service._tiers, "create_tier", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = created

            result = await rate_limit_service.create_tier(tier_in)

            assert result == created
            mock_create.assert_awaited_once_with(tier_in)

    @pytest.mark.asyncio
    async def test_get_tier_by_id_delegates(self, rate_limit_service, mock_db):
        tier = _tier(1, "free")
        with patch.object(rate_limit_service._tiers, "get_tier_by_id", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = tier

            result = await rate_limit_service.get_tier_by_id(1)
            assert result == tier
            mock_get.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_get_tier_by_id_not_found(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service._tiers, "get_tier_by_id", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ModelNotFoundError("Tier", 999)

            with pytest.raises(ModelNotFoundError):
                await rate_limit_service.get_tier_by_id(999)

    @pytest.mark.asyncio
    async def test_get_tier_by_name_delegates(self, rate_limit_service, mock_db):
        tier = _tier(2, "enterprise")
        with patch.object(rate_limit_service._tiers, "get_tier", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = tier

            result = await rate_limit_service.get_tier("enterprise")
            assert result == tier
            mock_get.assert_awaited_once_with("enterprise")

    @pytest.mark.asyncio
    async def test_update_tier_delegates(self, rate_limit_service, mock_db):
        with patch.object(rate_limit_service._tiers, "update_tier", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = MessageResponse(message="Tier updated")

            result = await rate_limit_service.update_tier("pro", TierUpdate())
            assert result == MessageResponse(message="Tier updated")
            mock_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_tier_rejects_rename(self, rate_limit_service, mock_db):
        """Rename rejection lives in the canonical TierService (asserted in tiers/test_service indirectly)."""
        with patch.object(rate_limit_service._tiers, "update_tier", new_callable=AsyncMock) as mock_update:
            mock_update.side_effect = DuplicateError("name", "renaming tier 'pro' is not supported")

            with pytest.raises(DuplicateError, match="renaming tier"):
                await rate_limit_service.update_tier("pro", TierUpdate(name="super-pro"))

    @pytest.mark.asyncio
    async def test_delete_tier_delegates(self, rate_limit_service, mock_db):
        """Deleting a tier in use raises the domain DuplicateError from the canonical service."""
        with patch.object(rate_limit_service._tiers, "delete_tier", new_callable=AsyncMock) as mock_delete:
            mock_delete.side_effect = DuplicateError("tier_in_use", "'busy_tier' (3 user(s), 0 rate limit(s))")

            with pytest.raises(DuplicateError):
                await rate_limit_service.delete_tier("busy_tier")

            mock_delete.assert_awaited_once_with("busy_tier")


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
            mock_get_tier.return_value = _tier(4, "premium")
            mock_rl_crud.exists = AsyncMock(return_value=False)
            mock_rl_crud.create = AsyncMock(return_value={"id": 1, "name": "premium_api_v1_data_100_60"})

            result = await rate_limit_service.create_rate_limit("premium", rl_create)

            mock_get_tier.assert_awaited_once_with("premium")
            assert result["id"] == 1

    def test_models_split_homes(self):
        """Tier lives in tiers.models, RateLimit in rate_limits.models; the relationship survives."""
        assert Tier.__module__ == "src.app.modules.tiers.models"
        assert RateLimit.__module__ == "src.app.modules.rate_limits.models"
        assert Tier.__tablename__ == "tier"
        assert RateLimit.__tablename__ == "rate_limit"
        assert hasattr(Tier, "rate_limits")
        assert hasattr(RateLimit, "tier")

    def test_factory_dependency(self, mock_db):
        """get_rate_limit_service returns initialized instance composing a TierService."""
        svc = get_rate_limit_service(db=mock_db)
        assert isinstance(svc, RateLimitService)
        assert svc.db is mock_db
        assert isinstance(svc._tiers, TierService)
        assert svc._tiers.db is mock_db

    def test_p1_18_single_tier_ownership(self, mock_db):
        """P1-18: tier business logic has exactly one implementation (TierService).

        RateLimitService keeps no tier_crud of its own; its tier shims
        delegate to the composed TierService, whose handles stay
        tier-primary (P0-1 decoupling contract, single owner).
        """
        rl_svc = RateLimitService(mock_db)
        t_svc = TierService(mock_db)

        assert not hasattr(rl_svc, "tier_crud")
        assert rl_svc.crud.model is RateLimit
        assert t_svc.crud.model is Tier
        assert t_svc.tier_crud.model is Tier
        assert t_svc.rate_limit_crud.model is RateLimit
        assert rl_svc._tiers.tier_crud.model is Tier
