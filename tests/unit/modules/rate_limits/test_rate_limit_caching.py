import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.rate_limits.schemas import RateLimitCreate, RateLimitUpdate
from src.app.modules.rate_limits.service import RateLimitService
from src.app.modules.tiers.schemas import TierRead

# Constants
TIER_ID = 1
CACHE_KEY = f"rate_limits:tier:{TIER_ID}"
RATE_LIMITS_DATA = [{"id": 1, "name": "test:limit", "limit": 100, "period": 60}]


def _tier_data() -> TierRead:
    """P2-C: the tier seam returns TierRead objects, not dicts."""
    return TierRead(id=TIER_ID, name="Free", created_at=datetime(2025, 1, 1, tzinfo=UTC))


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def service(mock_db):
    return RateLimitService(mock_db)


@pytest.mark.asyncio
async def test_get_rate_limits_by_tier_id_cache_miss(service):
    """Test that cache miss triggers DB query and sets cache."""

    # Mock Redis - patch at the module level where it's imported
    mock_redis = AsyncMock()
    with patch("src.app.modules.rate_limits.service.get_redis", return_value=mock_redis):
        mock_redis.get.return_value = None  # Cache miss

        # Mock page fetch (P0-5: single-RTT windowed COUNT seam)
        page_fetch = AsyncMock(return_value=RATE_LIMITS_DATA)
        with patch.object(service, "get_page_with_total", page_fetch):

            # Execute
            result = await service.get_rate_limits_by_tier_id(TIER_ID)

            # Verify result
            assert result == RATE_LIMITS_DATA

            # Verify Redis interactions (P2-B: get_with_lock reads once, then
            # re-reads after acquiring the refresh lock — double-checked
            # locking, so a miss costs exactly two GETs)
            assert mock_redis.get.call_count == 2
            mock_redis.get.assert_called_with(CACHE_KEY)
            mock_redis.setex.assert_called_once()

            # Verify args
            call_args = mock_redis.setex.call_args
            assert call_args[0][0] == CACHE_KEY
            assert call_args[0][1] == 3600  # TTL

            # Verify DB called
            page_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rate_limits_by_tier_id_cache_hit(service):
    """Test that cache hit returns cached data and skips DB."""

    cached_data = json.dumps(RATE_LIMITS_DATA)

    # Mock Redis - patch at the module level where it's imported
    mock_redis = AsyncMock()
    with patch("src.app.modules.rate_limits.service.get_redis", return_value=mock_redis):
        mock_redis.get.return_value = cached_data  # Cache hit

        # Mock CRUD
        with patch.object(service, "crud") as mock_crud:
            mock_crud.get_multi = AsyncMock()

            # Execute
            result = await service.get_rate_limits_by_tier_id(TIER_ID)

            # Verify result
            assert result == RATE_LIMITS_DATA

            # Verify Redis interactions
            mock_redis.get.assert_called_once_with(CACHE_KEY)
            mock_redis.setex.assert_not_called()

            # Verify DB NOT called
            mock_crud.get_multi.assert_not_called()


@pytest.mark.asyncio
async def test_invalidate_cache_on_create(service):
    """Test that creating a rate limit invalidates the cache."""
    tier_name = "Free"
    tier_data = _tier_data()

    # Mock Redis in the tiers.service namespace (P1-18: invalidation
    # lives in the canonical TierService).
    mock_redis = AsyncMock()
    with patch("src.app.modules.tiers.service.get_redis", return_value=mock_redis):

        # Mock internal methods
        service._get_tier_or_404 = AsyncMock(return_value=tier_data)

        with patch.object(service, "crud") as mock_crud:
            # Correctly mock async methods to return awaitables
            mock_crud.exists = AsyncMock(return_value=False)
            mock_crud.create = AsyncMock(return_value={"id": 1, "name": "new"})

            rate_limit_create = RateLimitCreate(path="/api", limit=10, period=60)

            # Execute
            await service.create_rate_limit(tier_name, rate_limit_create)

            # Verify invalidation
            mock_redis.delete.assert_called_once_with(CACHE_KEY)


@pytest.mark.asyncio
async def test_invalidate_cache_on_update(service):
    """Test that updating a rate limit invalidates the cache."""
    tier_name = "Free"
    tier_data = _tier_data()

    # Mock Redis in the tiers.service namespace (P1-18: invalidation
    # lives in the canonical TierService).
    mock_redis = AsyncMock()
    with patch("src.app.modules.tiers.service.get_redis", return_value=mock_redis):

        # Mock internal methods
        service._get_tier_or_404 = AsyncMock(return_value=tier_data)

        with patch.object(service, "crud") as mock_crud:
            # Correctly mock async methods
            mock_crud.get = AsyncMock(return_value={"id": 1})
            mock_crud.update = AsyncMock(return_value={"id": 1})

            values = RateLimitUpdate(limit=20)

            # Execute
            await service.update_rate_limit(tier_name, 1, values)

            # Verify invalidation
            mock_redis.delete.assert_called_once_with(CACHE_KEY)


@pytest.mark.asyncio
async def test_invalidate_cache_on_delete(service):
    """Test that deleting a rate limit invalidates the cache."""
    tier_name = "Free"
    tier_data = _tier_data()

    # Mock Redis in the tiers.service namespace (P1-18: invalidation
    # lives in the canonical TierService).
    mock_redis = AsyncMock()
    with patch("src.app.modules.tiers.service.get_redis", return_value=mock_redis):

        # Mock internal methods
        service._get_tier_or_404 = AsyncMock(return_value=tier_data)

        with patch.object(service, "crud") as mock_crud:
            # Correctly mock async methods
            mock_crud.get = AsyncMock(return_value={"id": 1})
            mock_crud.delete = AsyncMock(return_value=None)

            # Execute
            await service.delete_rate_limit(tier_name, 1)

            # Verify invalidation
            mock_redis.delete.assert_called_once_with(CACHE_KEY)
