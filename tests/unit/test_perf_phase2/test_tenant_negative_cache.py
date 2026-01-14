from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from src.app.middleware.tenant import TenantMiddleware


@pytest.fixture
def app():
    return FastAPI()


@pytest.fixture
def middleware(app):
    return TenantMiddleware(app)


@pytest.mark.asyncio
async def test_negative_cache_ttl(middleware):
    """Verify negative cache TTL is 60s (baseline) or 300s (target)."""
    slug = "nonexistent-tenant"

    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    # Mock DB (return None for tenant not found)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    # Patch dependencies
    with patch("src.app.middleware.tenant.local_session") as mock_local_session:
        mock_local_session.return_value.__aenter__.return_value = mock_session

        with patch("src.app.core.utils.cache.client", mock_redis):
            # Call the method directly
            result = await middleware._get_tenant_by_slug(slug)

            assert result is None

            # Verify setex called with "null" and TTL
            # Current code expects 60s
            # We check what it WAS called with
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args

            # args: key, time, value
            assert call_args[0][0] == f"tenant:slug:{slug}"
            ttl = call_args[0][1]
            assert call_args[0][2] == "null"

            # Verify TTL is now 300 (or whatever is in settings)
            assert ttl == 300
            print(f"Current Negative Cache TTL: {ttl}")
