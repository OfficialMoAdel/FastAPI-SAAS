"""Unit tests for feature_flags/utils.py tenant-JSONB read.

B7: the deprecated shims (is_feature_enabled / update_feature_flag /
bulk_update_feature_flags) were retired — FeatureFlagService is the single
flag module. These tests pin the remaining get_feature_flags read.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.feature_flags import utils as ff_utils


def _make_tenant(feature_flags):
    tenant = MagicMock()
    tenant.feature_flags = feature_flags
    return tenant


async def test_get_feature_flags_cache_hit():
    redis = MagicMock()
    redis.get = AsyncMock(return_value='{"beta": true}')

    with patch("src.app.core.utils.cache.client", redis):
        result = await ff_utils.get_feature_flags(db=MagicMock(), tenant_id="t1")
        assert result == {"beta": True}


async def test_get_feature_flags_cache_exception_falls_to_db():
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RuntimeError("down"))
    redis.set = AsyncMock()

    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch("src.app.core.utils.cache.client", redis):
        result = await ff_utils.get_feature_flags(db, "t1")
        assert result == {"a": 1}
        redis.set.assert_called_once()


async def test_get_feature_flags_no_redis():
    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch("src.app.core.utils.cache.client", None):
        assert await ff_utils.get_feature_flags(db, "t1") == {"a": 1}


async def test_get_feature_flags_cache_setex_exception():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(side_effect=RuntimeError("down"))

    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch("src.app.core.utils.cache.client", redis):
        assert await ff_utils.get_feature_flags(db, "t1") == {"a": 1}
