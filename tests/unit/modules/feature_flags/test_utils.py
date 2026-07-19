"""Unit tests for feature_flags/utils.py deprecated helper functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules._base import ModelNotFoundError
from src.app.modules.feature_flags import utils as ff_utils


def _make_tenant(feature_flags):
    tenant = MagicMock()
    tenant.feature_flags = feature_flags
    return tenant


async def test_is_feature_enabled_cache_hit_true():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"true")

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.is_feature_enabled(db=MagicMock(), tenant_id="t1", feature_name="beta")
        assert result is True


async def test_is_feature_enabled_cache_hit_false_bytes():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"false")

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.is_feature_enabled(db=MagicMock(), tenant_id="t1", feature_name="beta")
        assert result is False


async def test_is_feature_enabled_cache_hit_str():
    redis = MagicMock()
    redis.get = AsyncMock(return_value="TRUE")

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.is_feature_enabled(db=MagicMock(), tenant_id="t1", feature_name="beta")
        assert result is True


async def test_is_feature_enabled_cache_miss_db_default():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    db = MagicMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.is_feature_enabled(db=db, tenant_id="t1", feature_name="beta", default=True)
        assert result is True
        redis.setex.assert_awaited_once()


async def test_is_feature_enabled_cache_miss_db_flags():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    tenant = _make_tenant({"beta": False, "gamma": True})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", redis):
        assert await ff_utils.is_feature_enabled(db, "t1", "gamma") is True
        assert await ff_utils.is_feature_enabled(db, "t1", "beta") is False
        assert await ff_utils.is_feature_enabled(db, "t1", "missing", default=True) is True


async def test_is_feature_enabled_no_redis():
    tenant = _make_tenant({"beta": True})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", None):
        assert await ff_utils.is_feature_enabled(db, "t1", "beta") is True


async def test_is_feature_enabled_cache_get_exception():
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RuntimeError("down"))
    redis.setex = AsyncMock()

    tenant = _make_tenant({"beta": True})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", redis):
        assert await ff_utils.is_feature_enabled(db, "t1", "beta") is True


async def test_get_feature_flags_cache_hit():
    redis = MagicMock()
    redis.get = AsyncMock(return_value='{"beta": true}')

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.get_feature_flags(db=MagicMock(), tenant_id="t1")
        assert result == {"beta": True}


async def test_get_feature_flags_cache_exception_falls_to_db():
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RuntimeError("down"))
    redis.setex = AsyncMock()

    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", redis):
        result = await ff_utils.get_feature_flags(db, "t1")
        assert result == {"a": 1}
        redis.setex.assert_awaited_once()


async def test_get_feature_flags_no_redis():
    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", None):
        assert await ff_utils.get_feature_flags(db, "t1") == {"a": 1}


async def test_update_feature_flag_success():
    redis = MagicMock()
    redis.delete = AsyncMock()

    tenant = _make_tenant(None)
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        result = await ff_utils.update_feature_flag(db, "t1", "beta", True)
        assert result.feature_flags == {"beta": True}
        redis.delete.assert_awaited()


async def test_update_feature_flag_not_found():
    db = MagicMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(ff_utils, "redis_client", None):
        with pytest.raises(ModelNotFoundError):
            await ff_utils.update_feature_flag(db, "t1", "beta", True)


async def test_bulk_update_feature_flags_success():
    redis = MagicMock()
    redis.delete = AsyncMock()

    tenant = _make_tenant({"existing": False})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        result = await ff_utils.bulk_update_feature_flags(db, "t1", {"a": True, "b": False})
        assert result.feature_flags == {"existing": False, "a": True, "b": False}
        redis.delete.assert_awaited()


async def test_bulk_update_feature_flags_tenant_flags_none():
    redis = MagicMock()
    redis.delete = AsyncMock()

    tenant = _make_tenant(None)
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        result = await ff_utils.bulk_update_feature_flags(db, "t1", {"a": True})
        assert result.feature_flags == {"a": True}
        redis.delete.assert_awaited()


async def test_bulk_update_feature_flags_not_found():
    db = MagicMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(ff_utils, "redis_client", None):
        with pytest.raises(ModelNotFoundError):
            await ff_utils.bulk_update_feature_flags(db, "t1", {"a": True})


async def test_is_feature_enabled_cache_setex_exception():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(side_effect=RuntimeError("down"))

    tenant = _make_tenant({"beta": True})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", redis):
        assert await ff_utils.is_feature_enabled(db, "t1", "beta") is True


async def test_get_feature_flags_cache_setex_exception():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(side_effect=RuntimeError("down"))

    tenant = _make_tenant({"a": 1})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)

    with patch.object(ff_utils, "redis_client", redis):
        assert await ff_utils.get_feature_flags(db, "t1") == {"a": 1}


async def test_update_feature_flag_cache_delete_exception():
    redis = MagicMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("down"))

    tenant = _make_tenant(None)
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        result = await ff_utils.update_feature_flag(db, "t1", "beta", True)
        assert result.feature_flags == {"beta": True}


async def test_bulk_update_feature_flags_empty_keys_no_delete():
    redis = MagicMock()
    redis.delete = AsyncMock()

    tenant = _make_tenant({"existing": False})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        # Empty flags dict -> keys_to_delete empty -> delete(*[]) not awaited
        result = await ff_utils.bulk_update_feature_flags(db, "t1", {})
        assert result.feature_flags == {"existing": False}
        # Only the "features" key delete is awaited
        assert redis.delete.await_count == 1


async def test_bulk_update_feature_flags_cache_delete_exception():
    redis = MagicMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("down"))

    tenant = _make_tenant({"existing": False})
    db = MagicMock()
    db.get = AsyncMock(return_value=tenant)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(ff_utils, "redis_client", redis),
        patch("sqlalchemy.orm.attributes.flag_modified"),
    ):
        result = await ff_utils.bulk_update_feature_flags(db, "t1", {"a": True})
        assert result.feature_flags == {"existing": False, "a": True}
