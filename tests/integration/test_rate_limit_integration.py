import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from src.app.main import app
from src.app.core.config import settings
from src.app.core.utils.rate_limit import rate_limiter

class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)
    
    async def incr(self, key):
        val = self.store.get(key, 0)
        val = int(val) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return True

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
        return True

    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True
    
    async def aclose(self):
        pass

fake_redis_instance = FakeRedis()

import pytest_asyncio

@pytest_asyncio.fixture
async def async_client_with_mocked_redis():
    # Store original client and pool
    original_client = rate_limiter.client
    original_pool = rate_limiter.pool
    
    # Inject fake client
    rate_limiter.client = fake_redis_instance
    
    with patch("src.app.core.utils.rate_limit.RateLimiter.initialize") as mock_init:
        mock_init.return_value = None
        fake_redis_instance.store.clear()
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
        
    # Restore original
    rate_limiter.client = original_client
    rate_limiter.pool = original_pool

@pytest.mark.asyncio
async def test_rate_limit_headers(async_client_with_mocked_redis):
    # Hit /openapi.json usually safe public endpoint
    response = await async_client_with_mocked_redis.get("/openapi.json")
    
    # Fallback if 404 (though openapi usually exists)
    if response.status_code == 404:
        response = await async_client_with_mocked_redis.get("/docs")

    assert response.status_code == 200
    
    limit = settings.DEFAULT_RATE_LIMIT_LIMIT
    
    assert "X-RateLimit-Limit" in response.headers
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert "X-RateLimit-Remaining" in response.headers
    # 1st request
    assert response.headers["X-RateLimit-Remaining"] == str(limit - 1)

@pytest.mark.asyncio
async def test_rate_limit_exceeded(async_client_with_mocked_redis):
    limit = settings.DEFAULT_RATE_LIMIT_LIMIT
    
    # Consume all (limit is 10)
    for i in range(limit):
        await async_client_with_mocked_redis.get("/openapi.json")
    
    # Next one should differ
    response = await async_client_with_mocked_redis.get("/openapi.json")
    assert response.status_code == 429
    assert "Too Many Requests" in response.text
    assert response.headers["X-RateLimit-Remaining"] == "0"
