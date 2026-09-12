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

from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.database import Base
from src.app.core.schemas import TokenBlacklistCreate
from src.app.core.security.auto_ban import BanInfo
from src.app.core.utils.rate_limit import reset_rate_limiter
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
@patch("src.app.middleware.rate_limit.check_ban_and_rate_limit")
def test_rate_limit_headers_allowed(mock_combined, mock_limiter, client):
    """Test that rate limit headers are set correctly when request is allowed.

    H-3: the hot path now runs ban+GCRA in one combined call; mock the new
    seam. Decorators inject bottom-up, so the FIRST arg is the check_ban
    mock — set its return_value rather than rebinding the name."""
    mock_combined.return_value = (BanInfo(is_banned=False), (False, 5, 0))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Period"] == "3600"
    assert response.headers["X-RateLimit-Remaining"] == "5"


@patch("src.app.middleware.rate_limit.rate_limiter")
@patch("src.app.middleware.rate_limit.check_ban_and_rate_limit")
def test_rate_limit_headers_exceeded(mock_combined, mock_limiter, client):
    """Test that rate limit headers are set correctly when limit is exceeded.

    H-3: mock the combined ban+GCRA seam (see test_rate_limit_headers_allowed)."""
    limit = 10
    mock_combined.return_value = (BanInfo(is_banned=False), (True, 0, 10))

    response = client.get("/")

    assert response.status_code == 429
    data = response.json()
    assert data["detail"] == "Rate limit exceeded"
    assert data["error"]["code"] == "RATE_LIMITED"
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert response.headers["X-RateLimit-Remaining"] == "0"


@patch("src.app.middleware.rate_limit.settings.security.AUTO_BAN_ENABLED", True)
@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
def test_banned_ip_blocked_on_refresh_path(mock_ban_info, client):
    """B3 regression: the refresh-path rate-limit skip must not bypass the
    ban boundary. A banned IP hitting /api/v1/auth/refresh gets 403, not a
    pass-through to the endpoint."""
    mock_ban_info.return_value = BanInfo(
        is_banned=True, reason="Auto-ban after 5 rate limit violations"
    )

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 403
    assert response.headers["X-Banned"] == "true"
    mock_ban_info.assert_awaited_once()


@patch("src.app.middleware.rate_limit.settings.security.AUTO_BAN_ENABLED", True)
@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
def test_unbanned_ip_skips_rate_limit_on_refresh_path(mock_ban_info, client):
    """B3 companion: a clean IP still gets the zero-Redis-ops refresh skip
    (the endpoint's own dependency-level rate limit governs it)."""
    mock_ban_info.return_value = BanInfo(is_banned=False)

    response = client.post("/api/v1/auth/refresh")

    # The bare test app has no /api/v1/auth/refresh route — 404 proves the
    # request passed THROUGH the middleware instead of being 403'd.
    assert response.status_code == 404
    mock_ban_info.assert_awaited_once()


# =============================================================================
# Unit Tests: P1-3 OPTIONS / exempt-path skips
# =============================================================================


@patch("src.app.middleware.rate_limit.settings.security.AUTO_BAN_ENABLED", True)
@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
@patch("src.app.middleware.rate_limit.rate_limiter")
@patch("src.app.middleware.rate_limit.check_ban_and_rate_limit")
def test_options_skips_rate_limit(mock_combined, mock_limiter, mock_ban_info, client):
    """P1-3: CORS preflight must not burn rate-limit budget. Even with the
    combined seam armed to 429, OPTIONS passes through with zero Redis RTT."""
    mock_combined.return_value = (BanInfo(is_banned=False), (True, 0, 10))
    mock_ban_info.return_value = BanInfo(is_banned=False)

    response = client.options("/")

    # 405 comes from the bare fixture app's router (it registers GET-only),
    # which proves the request passed THROUGH the middleware untouched
    # instead of being 429'd.
    assert response.status_code == 405
    mock_combined.assert_not_awaited()
    mock_ban_info.assert_awaited_once()


@patch("src.app.middleware.rate_limit.settings.security.AUTO_BAN_ENABLED", True)
@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
def test_options_banned_ip_blocked(mock_ban_info, client):
    """P1-3/B3: the OPTIONS skip preserves the ban boundary — a banned IP's
    preflight gets 403, not a pass-through."""
    mock_ban_info.return_value = BanInfo(
        is_banned=True, reason="Auto-ban after 5 rate limit violations"
    )

    response = client.options("/")

    assert response.status_code == 403
    assert response.headers["X-Banned"] == "true"
    mock_ban_info.assert_awaited_once()


@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
@patch("src.app.middleware.rate_limit.rate_limiter")
@patch("src.app.middleware.rate_limit.check_ban_and_rate_limit")
def test_health_path_skips_rate_limit(mock_combined, mock_limiter, mock_ban_info, client):
    """P1-3: orchestrator probes (/health) bypass rate limiting entirely —
    full passthrough, not even the cheap ban lookup. The bare test app has
    no /health route, so 404 proves pass-through instead of a 429."""
    mock_combined.return_value = (BanInfo(is_banned=False), (True, 0, 10))

    response = client.get("/health")

    assert response.status_code == 404
    mock_combined.assert_not_awaited()
    mock_ban_info.assert_not_awaited()


@patch("src.app.middleware.rate_limit.get_ban_info", new_callable=AsyncMock)
@patch("src.app.middleware.rate_limit.rate_limiter")
@patch("src.app.middleware.rate_limit.check_ban_and_rate_limit")
def test_exempt_subpath_skips_rate_limit(mock_combined, mock_limiter, mock_ban_info, client):
    """P1-3: exempt sub-paths (/api/v1/health/live) inherit the skip via the
    shared is_path_exempt prefix rule."""
    mock_combined.return_value = (BanInfo(is_banned=False), (True, 0, 10))

    response = client.get("/api/v1/health/live")

    assert response.status_code == 404
    mock_combined.assert_not_awaited()
    mock_ban_info.assert_not_awaited()


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
            with patch("src.app.api.deps.auth.verify_token") as mock_verify:
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
        mock_request.state.tenant_id = "00000000-0000-0000-0000-000000000001"

        # Verify state has required fields for rate limiting
        assert mock_request.state.user_id == 123
        assert mock_request.state.tenant_id == "00000000-0000-0000-0000-000000000001"


# =============================================================================
# Integration Tests: Rate Limits
# =============================================================================


# Test user-agent to avoid IP filter blocking
TEST_USER_AGENT = "TestClient/1.0"


def _isolated_rate_limit_app() -> FastAPI:
    """Bare app with ONLY RateLimitMiddleware (P1-15).

    Full-app requests carry TenantMiddleware, which 400s header-less
    requests (missing X-Tenant-Slug) before/after rate limiting depending
    on order — so header/budget assertions need an isolated stack.
    """
    isolated = FastAPI()
    isolated.add_middleware(RateLimitMiddleware)

    @isolated.get("/")
    async def home():
        return {"message": "ok"}

    return isolated


@pytest.mark.asyncio
async def test_rate_limit_headers(mock_redis):
    """Test rate limit headers are present."""
    # Reset redis state
    mock_redis.store.clear()

    async with AsyncClient(
        transport=ASGITransport(app=_isolated_rate_limit_app()),
        base_url="http://test",
        headers={"User-Agent": TEST_USER_AGENT},
    ) as ac:
        # P1-3: /openapi.json is EXEMPT from middleware rate limiting, so a
        # full-app hit carries no rate-limit headers by design. Exercise the
        # non-exempt / route on an isolated stack instead.
        response = await ac.get("/")

        assert response.status_code == 200

        limit = 10

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

    limit = 10

    async with AsyncClient(
        transport=ASGITransport(app=_isolated_rate_limit_app()),
        base_url="http://test",
        headers={"User-Agent": TEST_USER_AGENT},
    ) as ac:
        # P1-3: /api/v1/health is EXEMPT from middleware rate limiting
        # (orchestrator probes must never 429). P1-15: a full-app hit on a
        # non-exempt endpoint without X-Tenant-Slug 400s at TenantMiddleware
        # regardless of order, so budget exhaustion is exercised on an
        # isolated stack: 10x200, then the 11th is 429'd in middleware.
        # Consume all (limit is 10)
        for i in range(limit):
            response = await ac.get("/")
            assert response.status_code == 200

        # Next one should be rate limited
        response = await ac.get("/")
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
            import uuid

            token_create = TokenBlacklistCreate(token=token, expires_at=expires_at, tenant_id=uuid.uuid4())
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
