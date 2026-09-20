"""Service-level caching tests for FeatureFlagService (B7 migration).

Replaces the retired utils-shim caching tests: FeatureFlagService is the
single flag module, so cache behavior is pinned here.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.feature_flags.service import FeatureFlagService


def _service(db):
    return FeatureFlagService(db=db)


@pytest.mark.asyncio
async def test_is_enabled_cached():
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="true")

    db = AsyncMock()
    service = _service(db)

    with patch("src.app.core.redis_provider.get_redis", return_value=redis):
        result = await service.is_enabled(tenant_id, "test_flag")

    assert result is True
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_is_enabled_cache_miss_falls_back_to_tenant_jsonb():
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    mock_tenant = MagicMock()
    mock_tenant.feature_flags = {"test_flag": True}
    db = AsyncMock()
    db.get = AsyncMock(return_value=mock_tenant)

    service = _service(db)

    with (
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=None)),
    ):
        result = await service.is_enabled(tenant_id, "test_flag")

    assert result is True
    redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_update_tenant_flag_invalidates_cache():
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.delete = AsyncMock()

    mock_tenant = MagicMock()
    mock_tenant.feature_flags = {}
    db = AsyncMock()
    db.get = AsyncMock(return_value=mock_tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    service = _service(db)

    with patch("src.app.core.redis_provider.get_redis", return_value=redis):
        await service.update_tenant_flag(tenant_id, "new_flag", True)

    assert mock_tenant.feature_flags == {"new_flag": True}
    assert redis.delete.await_count >= 1
