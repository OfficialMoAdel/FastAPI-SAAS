from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.users.models import User
from src.app.modules.users.service import UserService, _user_cache_key, _user_email_cache_key, _user_username_cache_key


@pytest.mark.asyncio
async def test_cache_user_uses_pipeline():
    """Test that _cache_user uses a Redis pipeline for optimization."""
    # Setup
    mock_db = AsyncMock()
    service = UserService(mock_db)

    user = MagicMock(spec=User)
    user.id = 1  # Must be int
    user.email = "test@example.com"
    user.username = "testuser"
    user.name = "Test User"  # Must be str
    user.is_deleted = False
    user.is_superuser = False
    user.profile_image_url = "https://example.com/profile.jpg"  # Must be str
    user.hashed_password = "hash"
    user.created_at = "2023-01-01"
    user.updated_at = "2023-01-01"
    user.tenant_id = 1
    user.tier_id = 1

    # Mock the Redis client
    with patch("src.app.modules.users.service.redis_client", new_callable=AsyncMock) as mock_redis:
        # Create a mock pipeline object
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()

        # Configure __aenter__ and __aexit__ for async context manager usage
        # __aenter__ must be an async function (or AsyncMock) that returns the pipeline
        mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
        mock_pipeline.__aexit__ = AsyncMock(return_value=None)

        # The pipeline() method itself is synchronous in redis.asyncio, returning the pipeline object
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        # Call the method
        await service._cache_user(user)

        # VERIFICATION

        # Check that pipeline was created with transaction=False
        mock_redis.pipeline.assert_called_once_with(transaction=False)

        # Check that setex was called on the pipeline 3 times
        assert mock_pipeline.setex.call_count == 3

        # Check execute was called
        mock_pipeline.execute.assert_called_once()

        # Check keys
        calls = mock_pipeline.setex.call_args_list
        keys = [call.args[0] for call in calls]
        assert _user_cache_key(user.id) in keys
        assert _user_email_cache_key(user.email) in keys
        assert _user_username_cache_key(user.username) in keys
