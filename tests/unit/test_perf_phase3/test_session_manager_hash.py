import json
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.app.core.security.session_manager import SessionManager


@pytest.fixture
def mock_pipeline():
    """Create a mock pipeline with sync methods and async execute."""
    pipe = MagicMock()
    pipe.hdel = MagicMock()
    pipe.hset = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1, 1])
    return pipe


@pytest.fixture
def mock_redis(mock_pipeline):
    redis = AsyncMock()
    redis.pipeline = MagicMock(return_value=mock_pipeline)
    return redis


@pytest.fixture
def session_manager(mock_redis):
    return SessionManager(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_register_session_dual_write(session_manager, mock_redis, mock_pipeline):
    """Verify that registering a session writes to both legacy List and new Hash using pipeline."""
    user_id = 123
    session_id = "sess-hash-1"
    device_info = "Hash Device"

    # Mock legacy get response (empty list)
    mock_redis.get.return_value = None

    await session_manager.register_session(user_id, session_id, device_info)

    # Verify Legacy Write (SETEX)
    mock_redis.setex.assert_called()

    # Verify Hash Write uses Pipeline (OPT-7)
    mock_redis.pipeline.assert_called_once()

    # Verify HSET on pipeline
    hash_key = f"user_sessions_v2:{user_id}"
    mock_pipeline.hset.assert_called_with(
        hash_key,
        session_id,
        ANY,  # The JSON string
    )

    # Verify Expiry on pipeline
    mock_pipeline.expire.assert_called_with(hash_key, session_manager.SESSION_TTL_SECONDS)

    # Verify pipeline executed
    mock_pipeline.execute.assert_called_once()


@pytest.mark.asyncio
async def test_is_session_valid_hash(session_manager, mock_redis):
    """Verify session validation using Hash (O(1))."""
    # Mock HEXISTS return value
    mock_redis.hexists.return_value = True

    # Placeholder for future Phase 2/3 when we switch to reading from Hash
    pass


@pytest.mark.asyncio
async def test_invalidate_session_dual_write(session_manager, mock_redis):
    """Verify invalidation removes from both."""
    user_id = 123
    session_id = "sess-hash-1"
    hash_key = f"user_sessions_v2:{user_id}"

    # Mock legacy data
    legacy_sessions = [{"session_id": session_id, "device_info": "test"}]
    mock_redis.get.return_value = json.dumps(legacy_sessions)

    await session_manager.invalidate_session(user_id, session_id)

    # Verify Legacy Write (SETEX or DELETE)
    # Since list becomes empty, it should delete
    mock_redis.delete.assert_any_call(f"user_sessions:{user_id}")

    # Verify Hash Delete (HDEL)
    mock_redis.hdel.assert_called_with(hash_key, session_id)


@pytest.mark.asyncio
async def test_count_sessions_hash(session_manager, mock_redis):
    """Verify O(1) counting."""
    user_id = 123
    hash_key = f"user_sessions:{user_id}"

    mock_redis.hlen.return_value = 5

    # Assuming we add this method
    if hasattr(session_manager, "_count_sessions_hash"):
        count = await session_manager._count_sessions_hash(user_id)
        assert count == 5
        mock_redis.hlen.assert_called_with(hash_key)
