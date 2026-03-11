import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.config import settings
from src.app.modules.security.service import IPFilterService


@pytest.fixture
def service():
    return IPFilterService()


@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock_pipe = AsyncMock()
    mock_pipe.__aenter__.return_value = mock_pipe
    mock.pipeline = MagicMock(return_value=mock_pipe)
    with patch("src.app.modules.security.service.cache.async_get_redis") as mock_get:
        async def mock_gen():
            yield mock
        mock_get.return_value = mock_gen()
        yield mock


@pytest.mark.asyncio
async def test_add_to_whitelist(service, mock_redis) -> None:
    result = await service.add_to_whitelist("1.2.3.4", reason="test")
    assert result is True
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_add_to_whitelist_with_expiry(service, mock_redis) -> None:
    expiry = datetime.now(UTC) + timedelta(hours=1)
    result = await service.add_to_whitelist("1.2.3.4", expires_at=expiry)
    assert result is True
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_add_to_whitelist_expired(service, mock_redis) -> None:
    expiry = datetime.now(UTC) - timedelta(hours=1)
    result = await service.add_to_whitelist("1.2.3.4", expires_at=expiry)
    assert result is False
    assert not mock_redis.setex.called
    assert not mock_redis.set.called


@pytest.mark.asyncio
async def test_remove_from_whitelist(service, mock_redis) -> None:
    mock_redis.delete.return_value = 1
    result = await service.remove_from_whitelist("1.2.3.4")
    assert result is True


@pytest.mark.asyncio
async def test_remove_from_whitelist_not_found(service, mock_redis) -> None:
    mock_redis.delete.return_value = 0
    result = await service.remove_from_whitelist("1.2.3.4")
    assert result is False


@pytest.mark.asyncio
async def test_add_to_blacklist(service, mock_redis) -> None:
    result = await service.add_to_blacklist("1.2.3.4", reason="test")
    assert result is True
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_add_to_blacklist_with_expiry(service, mock_redis) -> None:
    expiry = datetime.now(UTC) + timedelta(hours=1)
    result = await service.add_to_blacklist("1.2.3.4", expires_at=expiry)
    assert result is True
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_add_to_blacklist_expired(service, mock_redis) -> None:
    expiry = datetime.now(UTC) - timedelta(hours=1)
    result = await service.add_to_blacklist("1.2.3.4", expires_at=expiry)
    assert result is False
    assert not mock_redis.setex.called
    assert not mock_redis.set.called


@pytest.mark.asyncio
async def test_remove_from_blacklist(service, mock_redis) -> None:
    mock_redis.delete.return_value = 1
    result = await service.remove_from_blacklist("1.2.3.4")
    assert result is True


@pytest.mark.asyncio
async def test_is_whitelisted_disabled(service, mock_redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", False)
    result = await service.is_whitelisted("1.2.3.4")
    assert result is False
    assert not mock_redis.exists.called


@pytest.mark.asyncio
async def test_is_whitelisted_enabled_true(service, mock_redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "IP_WHITELIST_ENABLED", True)
    mock_redis.exists.return_value = 1
    result = await service.is_whitelisted("1.2.3.4")
    assert result is True


@pytest.mark.asyncio
async def test_is_blacklisted_disabled(service, mock_redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", False)
    result = await service.is_blacklisted("1.2.3.4")
    assert result is False
    assert not mock_redis.exists.called


@pytest.mark.asyncio
async def test_is_blacklisted_enabled_true(service, mock_redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "IP_BLACKLIST_ENABLED", True)
    mock_redis.exists.return_value = 1
    result = await service.is_blacklisted("1.2.3.4")
    assert result is True


@pytest.mark.asyncio
async def test_get_whitelist(service, mock_redis) -> None:
    mock_redis.scan.side_effect = [(0, ["key1", "key2"])]

    mock_data = json.dumps({"ip_address": "1.2.3.4", "added_at": "2023-01-01T00:00:00Z"}).encode()
    mock_redis.pipeline.return_value.execute.return_value = [mock_data, 3600, mock_data, -1]

    result = await service.get_whitelist()
    assert len(result) == 2
    assert "expires_at" in result[0]
    assert "expires_at" not in result[1]


@pytest.mark.asyncio
async def test_get_blacklist(service, mock_redis) -> None:
    mock_redis.scan.side_effect = [(0, ["key1", "key2"])]

    mock_data = json.dumps({"ip_address": "1.2.3.4", "added_at": "2023-01-01T00:00:00Z"}).encode()
    mock_redis.pipeline.return_value.execute.return_value = [mock_data, 3600, mock_data, -1]

    result = await service.get_blacklist()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_clear_whitelist(service, mock_redis) -> None:
    mock_redis.scan.side_effect = [(0, ["key1"])]
    mock_redis.delete.return_value = 1
    result = await service.clear_whitelist()
    assert result == 1


@pytest.mark.asyncio
async def test_clear_whitelist_empty(service, mock_redis) -> None:
    mock_redis.scan.side_effect = [(0, [])]
    result = await service.clear_whitelist()
    assert result == 0


@pytest.mark.asyncio
async def test_clear_blacklist(service, mock_redis) -> None:
    mock_redis.scan.side_effect = [(0, ["key1"])]
    mock_redis.delete.return_value = 1
    result = await service.clear_blacklist()
    assert result == 1
