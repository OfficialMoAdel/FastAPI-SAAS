"""Tests for rate limit middleware.

This module tests the RateLimitMiddleware in isolation from the main app.
Uses explicit singleton reset to ensure test isolation during coverage runs.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.core.config import settings
from src.app.core.utils.rate_limit import reset_rate_limiter
from src.app.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
def client():
    """Create test client with rate limit middleware.

    Resets the rate limiter singleton before and after each test to prevent
    state leakage during coverage runs.
    """
    # Reset singleton before test
    reset_rate_limiter()

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/")
    async def home():
        return {"message": "ok"}

    with TestClient(app) as test_client:
        yield test_client

    # Cleanup after test
    reset_rate_limiter()


@patch("src.app.middleware.rate_limit.rate_limiter")
def test_rate_limit_headers_allowed(mock_limiter, client):
    """Test that rate limit headers are set correctly when request is allowed."""
    # Setup mock to return allowed (False) and a count of 5
    mock_limiter.check_rate_limit = AsyncMock(return_value=(False, 5))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == str(settings.DEFAULT_RATE_LIMIT_LIMIT)
    assert response.headers["X-RateLimit-Period"] == str(settings.DEFAULT_RATE_LIMIT_PERIOD)

    # Remaining should be Limit - Count (e.g. 10 - 5 = 5)
    expected_remaining = max(0, settings.DEFAULT_RATE_LIMIT_LIMIT - 5)
    assert response.headers["X-RateLimit-Remaining"] == str(expected_remaining)


@patch("src.app.middleware.rate_limit.rate_limiter")
def test_rate_limit_headers_exceeded(mock_limiter, client):
    """Test that rate limit headers are set correctly when limit is exceeded."""
    # Setup mock to return limited (True) and a count equal to limit + 1
    limit = settings.DEFAULT_RATE_LIMIT_LIMIT
    mock_limiter.check_rate_limit = AsyncMock(return_value=(True, limit + 1))

    response = client.get("/")

    assert response.status_code == 429
    assert response.text == "Too Many Requests"
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert response.headers["X-RateLimit-Remaining"] == "0"
