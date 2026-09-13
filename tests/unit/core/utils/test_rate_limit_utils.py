from unittest.mock import AsyncMock, MagicMock, patch

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

    @pytest.mark.asyncio
    async def test_is_rate_limited_returns_false_if_under_limit(self):
        """Test is_rate_limited returns False (not limited) if count <= limit."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 5
        mock_redis.script_load.return_value = "sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "sha"

        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            # P2: is_rate_limited takes a pre-sanitized path (single caller
            # sanitizes for the tier-policy lookup first; re-sanitize dropped).
            is_limited = await limiter.is_rate_limited(db=MagicMock(), user_id=1, path="api", limit=10, period=60)

        assert is_limited is False

    @pytest.mark.asyncio
    async def test_is_rate_limited_returns_true_if_over_limit(self):
        """Test is_rate_limited returns True (limited) if count > limit."""
        mock_redis = AsyncMock()
        mock_redis.evalsha.return_value = 11
        mock_redis.script_load.return_value = "sha"

        limiter = RateLimiter()
        limiter._rate_limit_script_sha = "sha"

        with patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=mock_redis):
            # P2: is_rate_limited takes a pre-sanitized path (single caller
            # sanitizes for the tier-policy lookup first; re-sanitize dropped).
            is_limited = await limiter.is_rate_limited(db=MagicMock(), user_id=1, path="api", limit=10, period=60)

        assert is_limited is True

    @pytest.mark.asyncio
    async def test_is_rate_limited_uses_rate_policy_key_scheme(self):
        """P1-#5: is_rate_limited derives keys via rate_policy.build_rate_limit_key.

        The dep-layer path must share the middleware's key scheme — one key
        scheme, one decision module. The engine takes (key, policy) as inputs
        and never derives keys itself.
        """
        limiter = RateLimiter()
        captured: dict = {}

        async def fake_check(key: str, limit: int, period: int) -> tuple[bool, int, int]:
            captured["key"] = key
            captured["limit"] = limit
            captured["period"] = period
            return False, 1, 59

        with patch.object(limiter, "check_rate_limit_algorithm", side_effect=fake_check):
            with patch("src.app.core.tenancy.context.get_current_tenant", return_value=None):
                await limiter.is_rate_limited(db=MagicMock(), user_id=42, path="api", limit=10, period=60)

        # No tenant context: unscoped canonical key
        assert captured["key"] == "rate_limit:user:42:api"

        tenant_ctx = MagicMock()
        tenant_ctx.tenant_id = "t1"
        with patch.object(limiter, "check_rate_limit_algorithm", side_effect=fake_check):
            with patch("src.app.core.tenancy.context.get_current_tenant", return_value=tenant_ctx):
                await limiter.is_rate_limited(db=MagicMock(), user_id=42, path="api", limit=10, period=60)

        # Tenant context: build_tenant_key scoping (same shape the middleware uses)
        assert captured["key"] == "tenant:t1:rate_limit:user:42:api"

    @pytest.mark.asyncio
    async def test_is_rate_limited_respects_patched_build_rate_limit_key(self):
        """P1-#5: the fn-scope import keeps rate_policy.build_rate_limit_key patchable."""
        limiter = RateLimiter()
        fake_check = AsyncMock(return_value=(False, 1, 59))

        with patch("src.app.core.security.rate_policy.build_rate_limit_key", return_value="patched:key") as fake_key:
            with patch("src.app.core.tenancy.context.get_current_tenant", return_value=None):
                with patch.object(limiter, "check_rate_limit_algorithm", side_effect=fake_check):
                    await limiter.is_rate_limited(db=MagicMock(), user_id=1, path="api", limit=10, period=60)

        fake_key.assert_called_once()
        assert fake_check.call_args[0][0] == "patched:key"
