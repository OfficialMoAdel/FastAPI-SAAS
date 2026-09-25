"""Unit tests for Unified Rate-Policy Engine (Architecture Candidate 10)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.security.auto_ban import BanInfo
from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_rate_limit_key,
    build_verdict,
    check_security_budget,
    consume,
    resolve_rate_limits,
)
from src.app.core.security.request_admission import (
    RequestAdmissionService,
    _RateLimitContext,
)


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
    assert verdict.headers == {"X-RateLimit-Bypassed": "true"}


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
        patch(
            "src.app.core.security.request_admission.consume",
            new=AsyncMock(return_value=(False, 45, 0, False)),
        ),
    ):
        outcome = await RequestAdmissionService().evaluate(request)
    assert outcome.action == "admit"
    verdict = outcome.verdict
    assert isinstance(verdict, RateLimitVerdict)
    assert verdict.is_limited is False
    assert verdict.remaining == 45
    assert "X-RateLimit-Limit" in verdict.headers


# ---------------------------------------------------------------------------
# One verdict seam: header grammar and the GCRA degradation marker live in
# build_verdict / consume, so the admission path and the Security budgets
# cannot drift apart.
# ---------------------------------------------------------------------------


def test_build_verdict_owns_the_header_grammar():
    """One place assembles headers — both entry points return the same shape."""
    verdict = build_verdict(
        key="k", limit=10, period=60, is_limited=True, remaining=0, retry_after=7
    )
    assert verdict.headers == {
        "X-RateLimit-Limit": "10",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Period": "60",
        "Retry-After": "7",
    }
    assert verdict.is_limited is True


def test_build_verdict_omits_quota_headers_for_bypass():
    verdict = build_verdict(
        key="whitelisted",
        limit=None,
        period=None,
        is_limited=False,
        remaining=None,
        bypassed=True,
    )

    assert verdict.headers == {"X-RateLimit-Bypassed": "true"}
    assert verdict.limit is None
    assert verdict.period is None
    assert verdict.remaining is None


def test_build_verdict_marks_degradation():
    """A degraded check must be visible, not silently served as the configured
    algorithm (was: a warning log and an indistinguishable response)."""
    verdict = build_verdict(
        key="k", limit=10, period=60, is_limited=False, remaining=5, degraded=True
    )
    assert verdict.headers["X-RateLimit-Algorithm"] == "fixed-window (degraded)"


@pytest.mark.asyncio
async def test_consume_degrades_to_fixed_window_when_gcra_raises():
    """GCRA failure falls through to fixed window AND reports it."""
    gcra = MagicMock()
    gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("redis down"))

    with (
        patch("src.app.core.config.settings.rate_limit.RATE_LIMIT_ALGORITHM", "gcra"),
        patch(
            "src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter",
            return_value=gcra,
        ),
        patch(
            "src.app.core.security.rate_policy.default_rate_limiter.check_rate_limit",
            new=AsyncMock(return_value=(False, 3)),
        ),
    ):
        is_limited, remaining, _retry_after, degraded = await consume(
            "k", 10, 60
        )

    assert (is_limited, remaining) == (False, 7)
    assert degraded is True


@pytest.mark.asyncio
async def test_security_budget_reports_degradation():
    """The budget entry point surfaces the same degradation the path does."""
    gcra = MagicMock()
    gcra.check_rate_limit = AsyncMock(side_effect=RuntimeError("redis down"))

    with (
        patch("src.app.core.config.settings.rate_limit.RATE_LIMIT_ALGORITHM", "gcra"),
        patch(
            "src.app.core.utils.gcra_rate_limit.get_gcra_rate_limiter",
            return_value=gcra,
        ),
        patch(
            "src.app.core.security.rate_policy.default_rate_limiter.check_rate_limit",
            new=AsyncMock(return_value=(False, 0)),
        ),
    ):
        verdict = await check_security_budget("auth", "1.2.3.4")

    assert verdict.headers["X-RateLimit-Algorithm"] == "fixed-window (degraded)"


@pytest.mark.asyncio
async def _admit_with_engine(
    algorithm: str, composite: AsyncMock, ban_info: AsyncMock, consume_mock: AsyncMock
) -> None:
    """Drive one authenticated admission with a given configured engine."""
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/items", "headers": [], "client": ("1.2.3.4", 1234)}
    )
    ctx = _RateLimitContext(
        user_id="u1", tier_name=None, tenant_id=None, tier_id_raw=None,
        token="tok", jti=None, path="/api/v1/items",
    )
    with (
        patch("src.app.core.security.request_admission.settings.security.AUTO_BAN_ENABLED", True),
        patch("src.app.core.config.settings.rate_limit.RATE_LIMIT_ALGORITHM", algorithm),
        patch(
            "src.app.core.security.request_admission.RequestAdmissionService._get_rate_limit_config",
            new=AsyncMock(return_value=ctx),
        ),
        patch("src.app.core.security.request_admission.check_ban_blacklist_and_rate_limit", composite),
        patch("src.app.core.security.request_admission.get_ban_info", ban_info),
        patch("src.app.core.security.request_admission.consume", consume_mock),
    ):
        outcome = await RequestAdmissionService().evaluate(request)
    assert outcome.action == "admit"


@pytest.mark.asyncio
async def test_gcra_keeps_the_merged_composite():
    """Default engine: the 1-RTT composite path is preserved."""
    composite = AsyncMock(
        return_value=(False, 10, 60, "k", BanInfo(is_banned=False), (False, 9, 0))
    )
    await _admit_with_engine("gcra", composite, AsyncMock(), AsyncMock(return_value=(False, 9, 0, False)))
    composite.assert_awaited_once()


@pytest.mark.asyncio
async def test_fixed_window_bypasses_the_gcra_composite():
    """The composite hard-codes GCRA arithmetic, so a fixed-window operator
    must not be routed through it — otherwise the setting is silently
    ignored on the authenticated hot path."""
    composite = AsyncMock(
        return_value=(False, 10, 60, "k", BanInfo(is_banned=False), (False, 9, 0))
    )
    consume_mock = AsyncMock(return_value=(False, 9, 0, False))
    await _admit_with_engine(
        "fixed_window",
        composite,
        AsyncMock(return_value=BanInfo(is_banned=False)),
        consume_mock,
    )
    composite.assert_not_awaited()
    consume_mock.assert_awaited_once()
