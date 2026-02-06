from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.utils.cache import get_with_stampede_protection


@pytest.mark.asyncio
async def test_xfetch_uses_random_not_secrets():
    """Verify XFetch uses random.random() instead of secrets.randbelow()."""

    # Mock Redis
    mock_redis = AsyncMock()
    # Cache hit
    mock_redis.get.return_value = b'{"data": "old"}'
    # TTL is low enough to trigger probability check
    # If ttl=3600, remaining=100, delta=3500. prob = 1.0 * (3500/3600)^2 ~= 0.94
    mock_redis.ttl.return_value = 100

    fetch_func = AsyncMock(return_value={"data": "new"})

    with patch("src.app.core.utils.cache.client", mock_redis):
        # Disable Lua script to use the get/ttl path
        with patch("src.app.core.utils.cache.settings.XFETCH_USE_LUA_SCRIPT", False):
            # We only expect random to be present and used now
            with patch("src.app.core.utils.cache.random.random") as mock_random:
                mock_random.return_value = 0.0  # Force trigger if used

                # Patch _refresh_cache to avoid actual background task execution issues
                with patch("src.app.core.utils.cache._refresh_cache"):
                    await get_with_stampede_protection("key", fetch_func, ttl=3600)

                    # Assert random.random was called
                    assert mock_random.called
                    print("Used random.random")
