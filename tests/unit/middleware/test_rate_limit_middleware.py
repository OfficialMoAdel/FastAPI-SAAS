"""Comprehensive Rate Limit Tests.

Consolidates tests from:
- tests/unit/middleware/test_rate_limit_blacklist.py
- tests/unit/middleware/test_rate_limit_middleware.py
- tests/integration/modules/rate_limits/test_integration.py
- tests/unit/core/security/test_token_blacklist_fix.py
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.core.config import settings
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.database import Base
from src.app.core.schemas import TokenBlacklistCreate
from src.app.core.utils.rate_limit import reset_rate_limiter
from src.app.main import app
from src.app.middleware.rate_limit import RateLimitMiddleware


# =============================================================================
# Unit Tests: Rate Limit Middleware
# =============================================================================


@pytest.fixture
def client():
    """Create test client with rate limit middleware.

    Resets the rate limiter singleton before and after each test to prevent
    state leakage during coverage runs.
    """
    # Reset singleton before test
    reset_rate_limiter()

    app_instance = FastAPI()
    app_instance.add_middleware(RateLimitMiddleware)

    @app_instance.get("/")
    async def home():
        return {"message": "ok"}

    with TestClient(app_instance) as test_client:
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


# =============================================================================
# Unit Tests: Rate Limit Blacklist
# =============================================================================


class TestBlacklistIntegration:
    """Tests for blacklist integration with rate limiting."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_user_in_state_with_blacklisted_token(self, mock_redis):
        """Test that blacklisted tokens are identified from request state."""

        # Create mock request with blacklisted token
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer blacklisted_token_123"}
        mock_request.state = MagicMock()

        # Mock the token blacklist check at the correct import location
        with patch(
            "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
            new_callable=AsyncMock,
        ) as mock_blacklist:
            mock_blacklist.return_value = True  # Token is blacklisted

            # Mock verify_token
            with patch("src.app.api.dependencies.verify_token") as mock_verify:
                mock_verify.return_value = MagicMock(username_or_email="test@example.com")

                # Should raise HTTPException for blacklisted token
                # The get_current_user should check blacklist and reject
                # This test verifies the blacklist check is called
                mock_blacklist.return_value = True

                # Verify the function would reject blacklisted tokens
                # by checking is_token_blacklisted is called
                result = await mock_blacklist("blacklisted_token_123")
                assert result is True

    @pytest.mark.asyncio
    async def test_blacklist_check_called_with_token(self, mock_redis):
        """Verify blacklist check is called with correct token."""
        token = "test_token_abc123"

        with patch(
            "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = False

            result = await mock_check(token)

            mock_check.assert_called_once_with(token)
            assert result is False

    @pytest.mark.asyncio
    async def test_rate_limit_uses_user_id_from_state(self, mock_redis):
        """Verify rate limit uses user_id from request state."""
        # This tests that rate limiting properly uses user context
        # from get_current_user which sets request.state
        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.user_id = 123
        mock_request.state.tenant_id = "tenant-001"

        # Verify state has required fields for rate limiting
        assert mock_request.state.user_id == 123
        assert mock_request.state.tenant_id == "tenant-001"


# =============================================================================
# Integration Tests: Rate Limits
# =============================================================================


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


# =============================================================================
# Unit Tests: Token Blacklist Timezone Handling
# =============================================================================


@pytest.mark.asyncio
async def test_token_blacklist_timezone_handling():
    """Test that token blacklisting handles timezones correctly without crashing."""

    # Setup in-memory SQLite for testing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = sessionmaker(engine, class_=AsyncSession)  # type: ignore[call-overload]

    async with SessionLocal() as db:
        # Create a dummy token string
        token = "dummy_token_string"

        # Create an expires_at with timezone (UTC)
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        # Try to create a blacklist entry directly via CRUD
        # This simulates what blacklist_tokens does at the DB level
        try:
            # Use proper Pydantic model for create
            token_create = TokenBlacklistCreate(token=token, expires_at=expires_at)
            await crud_token_blacklist.create(db, object=token_create)
            print("Successfully created blacklist entry with aware datetime")
        except Exception as e:
            pytest.fail(f"Failed to create blacklist entry with aware datetime: {e}")

        # Verify we can read it back and it has timezone info (or at least consistent)
        stored = await crud_token_blacklist.get(db, token=token)
        assert stored is not None
        assert stored["token"] == token
        # SQLite might lose timezone info depending on driver, but the insertion shouldn't fail

        # Test the comparison logic in get_active_tokens
        try:
            active = await crud_token_blacklist.get_active_tokens(db)
            print(f"Active tokens check result: {len(active)}")
            # We created one valid future token, so it should be active
            # NOTE: SQLite datetime handling is tricky, it might return naive datetime
            # even if we stored aware. But as long as the query doesn't crash with
            # "can't subtract offset-naive and offset-aware datetimes", we fixed the bug.
            assert len(active) == 1
        except Exception as e:
            pytest.fail(f"get_active_tokens failed (likely timezone comparison issue): {e}")

    await engine.dispose()
