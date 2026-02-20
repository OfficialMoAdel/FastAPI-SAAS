import pytest
from httpx import ASGITransport, AsyncClient

from src.app.core.config import settings
from src.app.main import app

# Test user-agent to avoid IP filter blocking
TEST_USER_AGENT = "TestClient/1.0"


@pytest.mark.asyncio
async def test_rate_limit_headers(mock_redis):
    """Test rate limit headers are present."""
    # Reset redis state
    mock_redis.store.clear()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"User-Agent": TEST_USER_AGENT},
    ) as ac:
        # Hit /openapi.json usually safe public endpoint
        response = await ac.get("/openapi.json")

        # Fallback if 404 (though openapi usually exists)
        if response.status_code == 404:
            response = await ac.get("/docs")

        assert response.status_code == 200

        limit = settings.DEFAULT_RATE_LIMIT_LIMIT

        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == str(limit)
        assert "X-RateLimit-Remaining" in response.headers
        # 1st request
        assert response.headers["X-RateLimit-Remaining"] == str(limit - 1)


@pytest.mark.asyncio
async def test_rate_limit_exceeded(mock_redis):
    """Test rate limit exceeded behavior."""
    # Reset redis state
    mock_redis.store.clear()

    limit = settings.DEFAULT_RATE_LIMIT_LIMIT

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"User-Agent": TEST_USER_AGENT},
    ) as ac:
        # Use /api/v1/health endpoint which is rate-limited
        # Consume all (limit is 10)
        for i in range(limit):
            await ac.get("/api/v1/health")

        # Next one should be rate limited
        response = await ac.get("/api/v1/health")
        assert response.status_code == 429
        assert "Too Many Requests" in response.text or response.status_code == 429