from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules._base import ModelNotFoundError
from src.app.modules.feature_flags.schemas import (
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideUpdate,
    FeatureFlagUpdate,
)
from src.app.modules.feature_flags.service import FeatureFlagService, _LegacyFeatureFlagsService, warm_feature_flags


@pytest.fixture
def service(mock_db):
    return FeatureFlagService(db=mock_db)


@pytest.fixture
def mock_db():
    return AsyncMock()


def make_mock_flag(id=1, key="test", default_value=False, desc="desc", is_system=False):
    m = MagicMock()
    m.id = id
    m.key = key
    m.default_value = default_value
    m.description = desc
    m.value_type = "boolean"
    m.is_system = is_system
    return m


def make_mock_override(id=1, custom_value=None):
    m = MagicMock()
    m.id = id
    m.custom_value = custom_value
    return m


def make_mock_tenant(id, feature_flags={}):
    m = MagicMock()
    m.id = id
    m.feature_flags = feature_flags
    return m


@pytest.mark.asyncio
async def test_get_flag_by_key(service, mock_db):
    mock_flag = make_mock_flag(key="test_flag")
    with patch.object(service.crud, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_flag
        result = await service.get_flag_by_key("test_flag")
        assert result.id == 1
        mock_get.assert_awaited_once_with(db=mock_db, key="test_flag")


@pytest.mark.asyncio
async def test_get_flag_by_key_not_found(service, mock_db):
    with patch.object(service.crud, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.get_flag_by_key("test_flag")


@pytest.mark.asyncio
async def test_create_flag(service, mock_db):
    data = FeatureFlagCreate(key="test", description="desc", default_value=True, value_type="boolean")
    mock_flag = make_mock_flag()
    with patch.object(service, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_flag
        result = await service.create_flag(data)
        assert result.id == 1


@pytest.mark.asyncio
async def test_update_flag(service, mock_db):
    data = FeatureFlagUpdate(description="new desc")
    mock_flag = make_mock_flag(desc="new desc")
    with patch.object(service, "update", new_callable=AsyncMock) as mock_update:
        mock_update.return_value = mock_flag
        result = await service.update_flag(1, data)
        assert result.description == "new desc"


@pytest.mark.asyncio
async def test_update_flag_not_found(service, mock_db):
    data = FeatureFlagUpdate(description="new desc")
    with patch.object(service, "update", new_callable=AsyncMock) as mock_update:
        mock_update.return_value = None
        with pytest.raises(ModelNotFoundError):
            await service.update_flag(1, data)


@pytest.mark.asyncio
async def test_override_crud_operations(service, mock_db):
    t_id = uuid4()
    mock_override = make_mock_override(custom_value={"enabled": True})

    original_crud = service.override_crud
    
    mock_crud = MagicMock()
    mock_crud.get = AsyncMock(side_effect=[mock_override, None, mock_override])
    mock_crud.create = AsyncMock(return_value=mock_override)
    mock_crud.update = AsyncMock(side_effect=[mock_override, None])
    mock_crud.delete = AsyncMock()
    
    try:
        service.override_crud = mock_crud

        # Test get_override (returns mock first, then None)
        result = await service.get_override(1, t_id)
        assert result.id == 1

        result = await service.get_override(1, t_id)
        assert result is None

        # Test create_override
        create_data = FeatureFlagOverrideCreate(feature_flag_id=1, tenant_id=t_id, custom_value={"enabled": True})
        result = await service.create_override(create_data)
        assert result.id == 1

        # Test update_override (returns mock first, then None)
        update_data = FeatureFlagOverrideUpdate(custom_value={"enabled": False})
        
        result = await service.update_override(1, update_data)
        assert result.id == 1

        result = await service.update_override(1, update_data)
        assert result is None

        # Test delete_override
        await service.delete_override(1)
        mock_crud.delete.assert_awaited_once_with(db=mock_db, id=1)
        
    finally:
        service.override_crud = original_crud




@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_get_tenant_flags_cache_hit(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=b'{"test_flag": true}')
    t_id = uuid4()
    result = await service.get_tenant_flags(t_id)
    assert result == {"test_flag": True}
    mock_redis.get.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_get_tenant_flags_db_fallback(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    t_id = uuid4()

    mock_tenant = make_mock_tenant(t_id, {"db_flag": False})

    # Needs patch for Tenant model in get, or just direct mock
    mock_db.get = AsyncMock(return_value=mock_tenant)
    mock_redis.setex = AsyncMock()

    result = await service.get_tenant_flags(t_id)
    assert result == {"db_flag": False}
    mock_redis.setex.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_is_enabled_cache_hit(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=b"true")
    t_id = uuid4()

    result = await service.is_enabled(t_id, "test_flag")
    assert result is True


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_is_enabled_uses_override(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    t_id = uuid4()

    mock_flag = make_mock_flag(key="test_flag", default_value=False)
    mock_override = make_mock_override(custom_value={"enabled": True})

    with patch.object(service, "get_flag_by_key_or_none", new_callable=AsyncMock) as mock_get_flag:
        mock_get_flag.return_value = mock_flag
        with patch.object(service, "get_override", new_callable=AsyncMock) as mock_get_override:
            mock_get_override.return_value = mock_override
            mock_redis.setex = AsyncMock()

            result = await service.is_enabled(t_id, "test_flag")
            assert result is True


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_is_enabled_uses_flag_default(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    t_id = uuid4()

    mock_flag = make_mock_flag(key="test_flag", default_value=True)

    with patch.object(service, "get_flag_by_key_or_none", new_callable=AsyncMock) as mock_get_flag:
        mock_get_flag.return_value = mock_flag
        with patch.object(service, "get_override", new_callable=AsyncMock) as mock_get_override:
            mock_get_override.return_value = None
            mock_redis.setex = AsyncMock()

            result = await service.is_enabled(t_id, "test_flag")
            assert result is True


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_is_enabled_uses_tenant(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    t_id = uuid4()

    mock_tenant = make_mock_tenant(t_id, {"test_flag": True})
    mock_db.get = AsyncMock(return_value=mock_tenant)

    with patch.object(service, "get_flag_by_key_or_none", new_callable=AsyncMock) as mock_get_flag:
        mock_get_flag.return_value = None
        mock_redis.setex = AsyncMock()

        result = await service.is_enabled(t_id, "test_flag")
        assert result is True


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.flag_modified")
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_update_tenant_flag(mock_redis, mock_flag_mod, service, mock_db):
    t_id = uuid4()
    mock_tenant = make_mock_tenant(t_id, {})
    mock_db.get = AsyncMock(return_value=mock_tenant)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_redis.delete = AsyncMock()

    result = await service.update_tenant_flag(t_id, "test_flag", True)
    assert result.feature_flags["test_flag"] is True
    mock_db.commit.assert_awaited_once()
    mock_flag_mod.assert_called_once()


@pytest.mark.asyncio
async def test_update_tenant_flag_not_found(service, mock_db):
    mock_db.get = AsyncMock(return_value=None)
    with pytest.raises(ModelNotFoundError):
        await service.update_tenant_flag(uuid4(), "test_flag", True)


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.flag_modified")
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_delete_tenant_flag(mock_redis, mock_flag_mod, service, mock_db):
    t_id = uuid4()
    mock_tenant = make_mock_tenant(t_id, {"test_flag": True})
    mock_db.get = AsyncMock(return_value=mock_tenant)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_redis.delete = AsyncMock()

    result = await service.delete_tenant_flag(t_id, "test_flag")
    assert "test_flag" not in result.feature_flags
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_tenant_flag_not_found(service, mock_db):
    t_id = uuid4()
    mock_tenant = make_mock_tenant(t_id, {})
    mock_db.get = AsyncMock(return_value=mock_tenant)
    with pytest.raises(ModelNotFoundError):
        await service.delete_tenant_flag(t_id, "test_flag")


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.flag_modified")
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_bulk_update_tenant_flags(mock_redis, mock_flag_mod, service, mock_db):
    t_id = uuid4()
    mock_tenant = make_mock_tenant(t_id, {"old": True})
    mock_db.get = AsyncMock(return_value=mock_tenant)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_redis.delete = AsyncMock()

    result = await service.bulk_update_tenant_flags(t_id, {"new": False, "old": False})
    assert result.feature_flags["new"] is False
    assert result.feature_flags["old"] is False
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_service(mock_db):
    legacy = _LegacyFeatureFlagsService()
    t_id = uuid4()

    mock_flag = make_mock_flag(key="legacy_flag")

    with patch(
        "src.app.modules.feature_flags.service.FeatureFlagService.get_flag_by_key_or_none", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = mock_flag
        result = await legacy.get_flag(mock_db, "legacy_flag", t_id)
        assert result.id == 1

    with patch(
        "src.app.modules.feature_flags.service.FeatureFlagService.is_enabled", new_callable=AsyncMock
    ) as mock_is_enabled:
        mock_is_enabled.return_value = True
        result = await legacy.is_feature_enabled(mock_db, "legacy_flag", 1, t_id)
        assert result is True


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_warm_feature_flags(mock_redis, mock_db):
    mock_flag = make_mock_flag(key="warm_test")
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_flag]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_redis.setex = AsyncMock()

    count = await warm_feature_flags(mock_db)
    assert count == 1
    mock_redis.setex.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.app.modules.feature_flags.service.redis_client")
async def test_warm_feature_flags_exception(mock_redis, mock_db):
    mock_flag = make_mock_flag(key="warm_test")
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_flag]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_redis.setex = AsyncMock(side_effect=Exception("Redis error"))

    count = await warm_feature_flags(mock_db)
    assert count == 0
