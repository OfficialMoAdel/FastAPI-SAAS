from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


def _seam_cache(*, hit: dict | None = None):
    """A CacheClient double that drives the REAL read-through seam.

    The tier-policy read used to call ``stampede.get_with_lock`` with a raw
    Redis client, so these tests asserted on ``get``/``setex``. It now goes
    through ``cache_client.read_through``, so the double has to expose the
    seam's primitives (``get_json`` / ``set_nx`` / ``set_json`` /
    ``release_lock_if_owned``) and let the real implementation run over them.
    """
    from src.app.core.cache.read_through import read_through

    cache = MagicMock()
    cache.client = object()  # Redis is present
    cache.get_json = AsyncMock(return_value=hit)
    cache.set_nx = AsyncMock(return_value=True)
    cache.set_json = AsyncMock()
    cache.release_lock_if_owned = AsyncMock(return_value=True)

    async def _read_through(key, fetch, *, ttl, policy=None):
        return await read_through(cache, key, fetch, ttl=ttl, policy=policy)

    cache.read_through = _read_through
    return cache


@pytest.mark.asyncio
async def test_get_rate_limits_by_tier_id_cache_miss(service):
    """A cold key fetches once and publishes the value with the policy TTL."""

    page_fetch = AsyncMock(return_value=RATE_LIMITS_DATA)
    cache = _seam_cache()

    with (
        patch("src.app.modules.rate_limits.service.cache_client", cache),
        patch.object(service, "get_page_with_total", page_fetch),
    ):
        result = await service.get_rate_limits_by_tier_id(TIER_ID)

    assert result == RATE_LIMITS_DATA
    page_fetch.assert_awaited_once()

    # The value was published once, under the tier-policy key, with the
    # declared TTL — the single writer is the seam, not the caller.
    cache.set_json.assert_awaited_once()
    args, kwargs = cache.set_json.await_args
    assert args[0] == CACHE_KEY
    assert args[1] == RATE_LIMITS_DATA
    assert kwargs.get("ttl") == 3600

    # The claim was taken and ownership-released.
    cache.set_nx.assert_awaited_once()
    cache.release_lock_if_owned.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rate_limits_by_tier_id_cache_hit(service):
    """A warm key is served from the cache without touching the DB."""

    page_fetch = AsyncMock(return_value=RATE_LIMITS_DATA)
    cache = _seam_cache(hit=RATE_LIMITS_DATA)

    with (
        patch("src.app.modules.rate_limits.service.cache_client", cache),
        patch.object(service, "get_page_with_total", page_fetch),
    ):
        result = await service.get_rate_limits_by_tier_id(TIER_ID)

    assert result == RATE_LIMITS_DATA
    page_fetch.assert_not_awaited()
    cache.set_json.assert_not_awaited()
    cache.set_nx.assert_not_awaited(), "a hit must not take the claim"


@pytest.mark.asyncio
async def test_invalidate_cache_on_create(service):
    """Test that creating a rate limit invalidates the cache."""
    tier_name = "Free"
    tier_data = _tier_data()

    # Mock Redis at the shared-client global (A3: invalidation lives in
    # the canonical TierService and goes through the CacheClient seam,
    # which reads this global at call time).
    mock_redis = AsyncMock()
    with patch("src.app.core.utils.cache.client", mock_redis):

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

    # Mock Redis at the shared-client global (A3: invalidation lives in
    # the canonical TierService and goes through the CacheClient seam,
    # which reads this global at call time).
    mock_redis = AsyncMock()
    with patch("src.app.core.utils.cache.client", mock_redis):

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

    # Mock Redis at the shared-client global (A3: invalidation lives in
    # the canonical TierService and goes through the CacheClient seam,
    # which reads this global at call time).
    mock_redis = AsyncMock()
    with patch("src.app.core.utils.cache.client", mock_redis):

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
