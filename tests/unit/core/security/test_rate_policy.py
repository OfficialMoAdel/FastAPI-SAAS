"""Unit tests for Unified Rate-Policy Engine (Architecture Candidate 10)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_rate_limit_key,
    evaluate_rate_policy,
    resolve_rate_limits,
)


@pytest.mark.asyncio
async def test_resolve_rate_limits_from_cache():
    cached_policies = [
        {"path": "/api/v1/posts", "limit": 120, "period": 60},
        {"path": "/api/v1/comments", "limit": 30, "period": 60},
    ]
    with patch("src.app.core.security.rate_policy.get_tier_policy", new=AsyncMock(return_value=cached_policies)):
        limit, period = await resolve_rate_limits("/api/v1/posts", tier_id=1, tier_name="pro")
    assert limit == 120
    assert period == 60


@pytest.mark.asyncio
async def test_resolve_rate_limits_db_fallback():
    """P2: DB fallback resolves via a single get_multi (was: get + get_multi).

    The path match resolves in Python over the same row set; the write-back
    reuses the already-fetched rows.
    """
    mock_db = MagicMock()
    mock_rows = {"data": [{"path": "/api/v1/custom", "limit": 200, "period": 120}]}

    with (
        patch("fastcrud.FastCRUD.get_multi", new=AsyncMock(return_value=mock_rows)) as mock_get_multi,
        patch("fastcrud.FastCRUD.get", new=AsyncMock()) as mock_get,
    ):
        limit, period = await resolve_rate_limits("/api/v1/custom", tier_id=2, db=mock_db)
    assert limit == 200
    assert period == 120
    mock_get_multi.assert_awaited_once()
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_rate_limits_db_fallback_no_match():
    """P2: get_multi rows without the path fall through to settings (no write-back)."""
    mock_db = MagicMock()
    mock_rows = {"data": [{"path": "/api/v1/other", "limit": 10, "period": 60}]}

    with patch("fastcrud.FastCRUD.get_multi", new=AsyncMock(return_value=mock_rows)):
        limit, period = await resolve_rate_limits("/api/v1/custom", tier_id=2, db=mock_db)
    assert limit is not None
    assert period is not None


@pytest.mark.asyncio
async def test_resolve_rate_limits_settings_fallback():
    with patch("src.app.core.security.rate_policy.get_tier_policy", new=AsyncMock(return_value=None)):
        limit, period = await resolve_rate_limits("/api/v1/missing", tier_id=None, user_id=None)
    # Falls back to anonymous default
    assert limit is not None
    assert period is not None


def test_build_rate_limit_key():
    key_auth = build_rate_limit_key("/api/v1/users", user_id="u123", tenant_id="t1")
    assert "u123" in key_auth
    assert "/api/v1/users" in key_auth

    key_anon = build_rate_limit_key("/api/v1/users", user_id=None, anonymous_ip="1.2.3.4")
    assert "1.2.3.4" in key_anon
    assert "/api/v1/users" in key_anon


@pytest.mark.asyncio
async def test_evaluate_rate_policy_whitelisted():
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.ip_whitelisted = True

    with patch("src.app.core.security.rate_policy.settings") as mock_settings:
        mock_settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT = True
        verdict = await evaluate_rate_policy(request)

    assert isinstance(verdict, RateLimitVerdict)
    assert verdict.is_limited is False
    assert verdict.bypassed is True
    assert verdict.headers["X-RateLimit-Whitelisted"] == "true"


@pytest.mark.asyncio
async def test_evaluate_rate_policy_enforcement():
    request = MagicMock(spec=Request)
    request.url.path = "/api/v1/items"
    request.state = MagicMock(spec=[])

    mock_limiter = MagicMock()
    mock_limiter.check_rate_limit_algorithm = AsyncMock(return_value=(False, 45, 0))

    verdict = await evaluate_rate_policy(request, limiter=mock_limiter)
    assert verdict.is_limited is False
    assert verdict.remaining == 45
    assert "X-RateLimit-Limit" in verdict.headers
