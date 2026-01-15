import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.security.session_manager import SessionManager


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def session_manager(mock_redis):
    return SessionManager(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_migrate_sessions_to_hash(session_manager, mock_redis):
    """Verify migration of legacy sessions to hash format."""
    # Setup legacy data
    user_id_1 = "123"
    user_id_2 = "456"

    sessions_1 = [
        {"session_id": "s1", "device_info": "d1", "created_at": "2024-01-01"},
        {"session_id": "s2", "device_info": "d2", "created_at": "2024-01-02"},
    ]
    sessions_2 = [{"session_id": "s3", "device_info": "d3", "created_at": "2024-01-03"}]

    # Mock SCAN to return keys - scan_iter is an async generator
    async def mock_scan_iter(match):
        yield f"user_sessions:{user_id_1}"
        yield f"user_sessions:{user_id_2}"

    mock_redis.scan_iter = mock_scan_iter

    # Mock GET to return session data
    async def mock_get(key):
        if key == f"user_sessions:{user_id_1}":
            return json.dumps(sessions_1)
        if key == f"user_sessions:{user_id_2}":
            return json.dumps(sessions_2)
        return None

    mock_redis.get = AsyncMock(side_effect=mock_get)

    # Mock pipeline - redis.pipeline() returns an async context manager
    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()

    # Create an async context manager for the pipeline
    async_cm = AsyncMock()
    async_cm.__aenter__.return_value = mock_pipeline
    async_cm.__aexit__.return_value = None

    # redis.pipeline() should return the async context manager
    mock_redis.pipeline = MagicMock(return_value=async_cm)

    # Execute migration
    count = await session_manager.migrate_to_hash()

    # Verify results
    assert count == 2  # 2 users migrated

    # Verify pipeline execution
    assert mock_pipeline.execute.called

    # Check that HSET was called for user 1
    # Note: The implementation uses json_dumps which produces compact JSON (no spaces)
    hash_key_1 = f"user_sessions_v2:{user_id_1}"
    mock_pipeline.hset.assert_any_call(hash_key_1, "s1", json.dumps(sessions_1[0], separators=(",", ":")))
    mock_pipeline.hset.assert_any_call(hash_key_1, "s2", json.dumps(sessions_1[1], separators=(",", ":")))
    mock_pipeline.expire.assert_any_call(hash_key_1, session_manager.SESSION_TTL_SECONDS)

    # user 2
    hash_key_2 = f"user_sessions_v2:{user_id_2}"
    mock_pipeline.hset.assert_any_call(hash_key_2, "s3", json.dumps(sessions_2[0], separators=(",", ":")))
    mock_pipeline.expire.assert_any_call(hash_key_2, session_manager.SESSION_TTL_SECONDS)
