from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.utils.rate_limit import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter utility."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset RateLimiter singleton before and after each test."""
        RateLimiter._instance = None
        yield
        RateLimiter._instance = None

    @pytest.mark.asyncio
    async def test_check_rate_limit_uses_lua_script(self):
        """Test that check_rate_limit executes via EVALSHA (Lua) for atomicity."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 1

        # We must patch get_client because conftest.py globally patches it to return FakeRedis
        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            result, count = await RateLimiter().check_rate_limit("test_key", 10, 60)

        assert result is False
        assert count == 1

        # Verify script execution: args = (sha, numkeys, key, args...)
        mock_redis.evalsha.assert_called_once()
        call_args = mock_redis.evalsha.call_args
        assert call_args[0][1] == 1  # numkeys
        assert "ratelimit:test_key" in call_args[0][2]

        mock_redis.incr.assert_not_called()
        mock_redis.expire.assert_not_called()
