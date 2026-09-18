"""Unit tests for Unified Rate-Policy Engine (Architecture Candidate 10)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.security.auto_ban import BanInfo
from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_rate_limit_key,
    resolve_rate_limits,
)
from src.app.core.security.request_admission import RequestAdmissionService


@pytest.mark.asyncio
async def test_resolve_rate_limits_from_cache():
    with patch(
        "src.app.core.security.rate_policy.get_tier_policy_limits",
        new=AsyncMock(return_value=(120, 60)),
    ):
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
    with patch(
        "src.app.core.security.rate_policy.get_tier_policy_limits",
        new=AsyncMock(return_value=(None, None)),
    ):
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
async def test_request_admission_whitelisted():
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/items", "headers": [], "client": ("1.2.3.4", 1234)}
    )
    request.state.ip_whitelisted = True

    with patch("src.app.core.security.request_admission.settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT", True):
        outcome = await RequestAdmissionService().evaluate(request)

    assert outcome.action == "admit"
    verdict = outcome.verdict
    assert isinstance(verdict, RateLimitVerdict)
    assert verdict.is_limited is False
    assert verdict.bypassed is True
    assert verdict.headers["X-RateLimit-Whitelisted"] == "true"


@pytest.mark.asyncio
async def test_request_admission_whitelisted_banned_rejects():
    """P1: the whitelist branch bypasses the *counter*, never the ban — a
    whitelisted-but-banned IP must get reject_banned, not a synthetic admit."""
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/items", "headers": [], "client": ("1.2.3.4", 1234)}
    )
    request.state.ip_whitelisted = True

    with (
        patch("src.app.core.security.request_admission.settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT", True),
        patch("src.app.core.security.request_admission.settings.security.AUTO_BAN_ENABLED", True),
        patch(
            "src.app.core.security.request_admission.get_ban_info",
            new=AsyncMock(return_value=BanInfo(is_banned=True, reason="auto-ban")),
        ),
    ):
        outcome = await RequestAdmissionService().evaluate(request)

    assert outcome.action == "reject_banned"
    assert outcome.ban_info is not None
    assert outcome.ban_info.is_banned is True
    assert outcome.verdict is None


@pytest.mark.asyncio
async def test_rate_limit_bucket_key_is_case_insensitive():
    """P1: exempt decisions match on the lowercased path, so the bucket key
    must too — /API/V1/X and /api/v1/x share ONE bucket (no budget doubling)."""
    from src.app.core.utils.path_utils import sanitize_path

    service = RequestAdmissionService()
    upper = Request(
        {"type": "http", "method": "GET", "path": "/API/V1/USERS", "headers": [], "client": ("1.2.3.4", 1234)}
    )
    lower = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/users", "headers": [], "client": ("1.2.3.4", 1234)}
    )

    ctx_upper = await service._get_rate_limit_config(upper)
    ctx_lower = await service._get_rate_limit_config(lower)

    assert ctx_upper.path == ctx_lower.path
    assert ctx_upper.path == sanitize_path("/api/v1/users")


@pytest.mark.asyncio
async def test_request_admission_enforcement():
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/items", "headers": [], "client": ("1.2.3.4", 1234)}
    )
    request.state.ip_whitelisted = False
    with (
        patch("src.app.core.security.request_admission.settings.security.AUTO_BAN_ENABLED", False),
        patch("src.app.core.security.request_admission.rate_limiter") as mock_limiter,
    ):
        mock_limiter.check_rate_limit_algorithm = AsyncMock(return_value=(False, 45, 0))
        outcome = await RequestAdmissionService().evaluate(request)
    assert outcome.action == "admit"
    verdict = outcome.verdict
    assert isinstance(verdict, RateLimitVerdict)
    assert verdict.is_limited is False
    assert verdict.remaining == 45
    assert "X-RateLimit-Limit" in verdict.headers
