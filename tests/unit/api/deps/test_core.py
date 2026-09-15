"""Tests for the rate-policy engine's security-budget verb.

These sit at the engine interface (``check_security_budget``) — the seam
every endpoint brute-force budget crosses (auth login, MFA, passkey, magic
link, AI chat). The historical ``api/deps/core.rate_limiter_dependency``
tests were deleted with the dependency: its tier-resolution behaviour is
covered by the rate_policy tests; the ASGI middleware path by
tests/unit/middleware.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.security.rate_policy import check_security_budget


def _make_limiter(is_limited=False, remaining=5, retry_after=0):
    limiter = MagicMock()
    limiter.check_rate_limit_algorithm = AsyncMock(return_value=(is_limited, remaining, retry_after))
    return limiter


async def test_budget_uses_configured_limit_and_period():
    """The budget name resolves (limit, period) from SECURITY_BUDGETS."""
    limiter = _make_limiter()

    verdict = await check_security_budget("auth", "1.2.3.4", limiter=limiter)

    assert not verdict.is_limited
    args = limiter.check_rate_limit_algorithm.call_args.args
    assert args[1:] == (5, 60)  # auth budget: 5 attempts / 60s
    assert verdict.limit == 5
    assert verdict.period == 60
    assert verdict.headers["X-RateLimit-Limit"] == "5"


async def test_budget_key_is_namespaced_per_budget_and_identity():
    """Keys isolate budgets from each other and from path-shaped keys."""
    limiter = _make_limiter()

    await check_security_budget("mfa_verify", "42", limiter=limiter)
    await check_security_budget("mfa_mutate", "42", limiter=limiter)
    await check_security_budget("mfa_verify", "43", limiter=limiter)

    keys = [c.args[0] for c in limiter.check_rate_limit_algorithm.call_args_list]
    # P1-5 separation: verify and mutate budgets never share a key.
    assert keys[0] != keys[1]
    # Different identities never share a key.
    assert keys[0] != keys[2]
    assert keys[0] == "rate_limit:user:42:budget:mfa_verify"


async def test_budget_key_is_tenant_scoped_when_tenant_id_passed():
    """Tenant context prefixes the canonical key — the same tenant-scoping
    the ASGI middleware path relies on (ported from the deleted
    is_rate_limited tenant-key coverage)."""
    limiter = _make_limiter()

    await check_security_budget("mfa_verify", "42", tenant_id="t1", limiter=limiter)

    key = limiter.check_rate_limit_algorithm.call_args.args[0]
    assert key == "tenant:t1:rate_limit:user:42:budget:mfa_verify"


async def test_budget_limited_verdict_carries_retry_after():
    limiter = _make_limiter(is_limited=True, remaining=0, retry_after=37)

    verdict = await check_security_budget("auth", "1.2.3.4", limiter=limiter)

    assert verdict.is_limited
    assert verdict.retry_after == 37
    assert verdict.headers["Retry-After"] == "37"
    assert verdict.headers["X-RateLimit-Remaining"] == "0"


async def test_budget_unknown_name_fails_loud():
    """An unconfigured budget is a caller typo — never a silent default."""
    with pytest.raises(KeyError):
        await check_security_budget("nonexistent_budget", "x", limiter=_make_limiter())


async def test_budget_uses_configured_engine_algorithm():
    """Budgets run the shared algorithm selector (GCRA/fixed-window),
    not a module-local fixed-window shortcut like the old per-channel limiters."""
    limiter = _make_limiter()

    await check_security_budget("ai_chat", "7", limiter=limiter)

    method = limiter.check_rate_limit_algorithm
    assert method.await_count == 1  # single engine call, no hand-rolled scripts
