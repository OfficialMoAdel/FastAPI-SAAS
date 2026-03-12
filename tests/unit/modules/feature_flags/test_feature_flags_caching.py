from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.feature_flags import utils


@pytest.fixture
def mock_redis():
    with patch("src.app.modules.feature_flags.utils.redis_client", new_callable=AsyncMock) as m:
        yield m


@pytest.mark.asyncio
async def test_is_feature_enabled_cached(mock_redis):
    tenant_id = uuid4()

    # Setup cache hit
    mock_redis.get.return_value = b"true"

    db = AsyncMock()

    result = await utils.is_feature_enabled(db, tenant_id, "test_flag")

    assert result is True
    mock_redis.get.assert_called_with(f"tenant:{tenant_id}:feature:test_flag")
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_is_feature_enabled_cache_miss(mock_redis):
    tenant_id = uuid4()

    # Setup cache miss
    mock_redis.get.return_value = None

    db = AsyncMock()
    mock_tenant = MagicMock()
    mock_tenant.feature_flags = {"test_flag": True}
    db.get.return_value = mock_tenant

    result = await utils.is_feature_enabled(db, tenant_id, "test_flag")

    assert result is True
    # Verify cache set
    mock_redis.setex.assert_called_with(f"tenant:{tenant_id}:feature:test_flag", 300, "true")


@pytest.mark.asyncio
async def test_update_invalidates_cache(mock_redis):
    tenant_id = uuid4()
    db = AsyncMock()
    mock_tenant = MagicMock()
    mock_tenant.feature_flags = {}
    db.get.return_value = mock_tenant

    await utils.update_feature_flag(db, tenant_id, "new_flag", True)

    # Verify deletions
    assert mock_redis.delete.call_count == 2
    mock_redis.delete.assert_any_call(f"tenant:{tenant_id}:feature:new_flag")
    mock_redis.delete.assert_any_call(f"tenant:{tenant_id}:features")
