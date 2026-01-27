import pytest
from unittest.mock import Mock, patch, AsyncMock
from src.app.core.monitoring.r1_unified_metrics import get_r1_metrics, reset_r1_metrics


@pytest.mark.asyncio
async def test_fail_open_metrics_increment():
    """Verify that record_fail_open increments the counter."""
    reset_r1_metrics()
    metrics = get_r1_metrics()

    # Initial state
    assert metrics.get_counter("r1_security_fail_open_total") == 0

    # Import and call
    from src.app.core.monitoring.r1_unified_metrics import record_fail_open

    record_fail_open("test")

    assert metrics.get_counter("r1_security_fail_open_total") == 1


@pytest.mark.asyncio
async def test_session_manager_fail_open():
    """Verify session manager records metric on Redis failure."""
    from src.app.core.security.session_manager import SessionManager

    reset_r1_metrics()
    metrics = get_r1_metrics()

    # Initialize without Redis client
    manager = SessionManager(redis_client=None)

    # Check session validity (should fail open)
    result = await manager.is_session_valid("user1", "sess1")

    assert result is True
    assert metrics.get_counter("r1_security_fail_open_total") == 1


@pytest.mark.asyncio
async def test_token_blacklist_fail_open():
    """Verify token blacklist records metric on Redis failure."""
    from src.app.core.security import token_blacklist_cache

    reset_r1_metrics()
    metrics = get_r1_metrics()

    # Mock redis_client being None in the module
    with patch("src.app.core.security.token_blacklist_cache.redis_client", None):
        result = await token_blacklist_cache.is_token_blacklisted("token")

        assert result is None
        assert metrics.get_counter("r1_security_fail_open_total") == 1


@pytest.mark.asyncio
async def test_rate_limit_fail_open():
    """Verify rate limiter records metric on failure when configured to fail open."""
    from src.app.core.utils.rate_limit import RateLimiter
    from src.app.core.config import settings

    reset_r1_metrics()
    metrics = get_r1_metrics()

    limiter = RateLimiter()

    # Mock get_client to raise Exception
    with (
        patch.object(RateLimiter, "get_client", side_effect=Exception("Redis down")),
        patch("src.app.core.config.settings.RATE_LIMIT_FAIL_OPEN", True),
    ):
        is_limited, _ = await limiter.check_rate_limit("key", 10, 60)

        assert is_limited is False  # Open
        assert metrics.get_counter("r1_security_fail_open_total") == 1
