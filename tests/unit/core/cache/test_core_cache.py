from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.utils.cache import cache


@pytest.mark.asyncio
async def test_cache_decorator_uses_setex():
    """Test that the cache decorator uses atomic setex instead of set + expire."""

    # Mock Redis client
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    # Mock the module-level client
    with patch("src.app.core.utils.cache.client", mock_redis):
        # Define a dummy endpoint
        @cache(key_prefix="test", expiration=60)
        async def dummy_endpoint(request: Request, id: int):
            return {"data": "value"}

        # Create a dummy request
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"

        # Call the endpoint
        result = await dummy_endpoint(mock_request, id=123)

        # Verify result
        assert result == {"data": "value"}

        # Verify Redis calls
        # Should call setex once with correct args
        # Note: redis-py setex args are (name, time, value)
        mock_redis.setex.assert_called_once()

        # Should NOT call set or expire separately
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

        # Verify arguments
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "test:123"  # key
        assert call_args[0][1] == 60  # time

        # Value is bytes because json_dumps returns bytes by default
        val = call_args[0][2]
        assert isinstance(val, bytes)
        assert b'"data":"value"' in val.replace(b" ", b"")  # Remove spaces for robust JSON check
