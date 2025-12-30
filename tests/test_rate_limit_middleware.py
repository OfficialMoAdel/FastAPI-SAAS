import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.core.config import settings

@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/")
    async def home():
        return {"message": "ok"}

    return TestClient(app)

@patch("src.app.middleware.rate_limit.rate_limiter")
def test_rate_limit_headers_allowed(mock_limiter, client):
    # Setup mock to return allowed (False) and a count of 5
    mock_limiter.check_rate_limit = AsyncMock(return_value=(False, 5))
    
    response = client.get("/")
    
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == str(settings.DEFAULT_RATE_LIMIT_LIMIT)
    assert response.headers["X-RateLimit-Period"] == str(settings.DEFAULT_RATE_LIMIT_PERIOD)
    
    # Remaining should be Limit - Count (e.g. 10 - 5 = 5)
    # Assuming default limit is 10. Let's check config, but dynamic calculation is:
    expected_remaining = max(0, settings.DEFAULT_RATE_LIMIT_LIMIT - 5)
    assert response.headers["X-RateLimit-Remaining"] == str(expected_remaining)

@patch("src.app.middleware.rate_limit.rate_limiter")
def test_rate_limit_headers_exceeded(mock_limiter, client):
    # Setup mock to return limited (True) and a count equal to limit + 1
    limit = settings.DEFAULT_RATE_LIMIT_LIMIT
    mock_limiter.check_rate_limit = AsyncMock(return_value=(True, limit + 1))
    
    response = client.get("/")
    
    assert response.status_code == 429
    assert response.text == "Too Many Requests"
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert response.headers["X-RateLimit-Remaining"] == "0"
