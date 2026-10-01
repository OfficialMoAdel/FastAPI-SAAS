"""Tests for the security-budget seam's Verdict behaviour.

These sit at the interface every endpoint brute-force budget crosses (auth
login, MFA, passkey, magic link, AI chat). The historical
``api/deps/core.rate_limiter_dependency`` tests were deleted with the
dependency: its tier-resolution behaviour is covered by the rate_policy tests;
the ASGI middleware path by tests/unit/middleware.

Note on the MFA keys below: ``rate_limit:user:42:budget:mfa_verify`` is the
**step-up door's** meter (POST /mfa/verify, authenticated, account known up
front). It is not a defect and not a duplicate — the complete-login door meters
separately under a pending token and under the per-account backstop. See
``tests/unit/core/security/test_security_budget_contract.py`` for the golden
key table covering all three.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.budgets import Identity, SecurityAction, probe


def _make_limiter(is_limited=False, remaining=5, retry_after=0):
    limiter = MagicMock()
    limiter.check_rate_limit_algorithm = AsyncMock(return_value=(is_limited, remaining, retry_after))
    return limiter


async def test_budget_uses_configured_limit_and_period():
    """The budget name resolves (limit, period) from SECURITY_BUDGETS."""
    limiter = _make_limiter()

    verdict = await probe(SecurityAction.AUTH, Identity(ip="1.2.3.4"), limiter=limiter)

    assert not verdict.is_limited
    args = limiter.check_rate_limit_algorithm.call_args.args
    assert args[1:] == (5, 60)  # auth budget: 5 attempts / 60s
    assert verdict.limit == 5
    assert verdict.period == 60
    assert verdict.headers["X-RateLimit-Limit"] == "5"


async def test_budget_key_is_namespaced_per_budget_and_identity():
    """Keys isolate budgets from each other and from path-shaped keys."""
    limiter = _make_limiter()

    await probe(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), limiter=limiter)
    await probe(SecurityAction.MFA_MUTATE, Identity(user_id="42"), limiter=limiter)
    await probe(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="43"), limiter=limiter)

    keys = [c.args[0] for c in limiter.check_rate_limit_algorithm.call_args_list]
    # P1-5 separation: verify and mutate budgets never share a key.
    assert keys[0] != keys[1]
    # Different identities never share a key.
    assert keys[0] != keys[2]
    # The step-up door's meter, byte-for-byte what the router emits today.
    assert keys[0] == "rate_limit:user:42:budget:mfa_verify"


async def test_budget_key_is_never_tenant_scoped():
    """P1-9: budget keys are installation-wide by design.

    The seam used to accept a ``tenant_id`` that no production caller
    supplied, and this test proved tenant scoping worked — against a
    capability nothing exercised. The parameter is removed, and the global
    key is now the only reachable form.

    The IP-keyed brute-force budgets MUST stay global: scoping them would
    let an attacker mint a fresh budget by varying the tenant header. See
    budgets.py, "Tenant scope: DELIBERATELY GLOBAL".
    """
    limiter = _make_limiter()

    await probe(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), limiter=limiter)

    key = limiter.check_rate_limit_algorithm.call_args.args[0]
    assert key == "rate_limit:user:42:budget:mfa_verify"
    assert not key.startswith("tenant:")


async def test_budget_limited_verdict_carries_retry_after():
    limiter = _make_limiter(is_limited=True, remaining=0, retry_after=37)

    verdict = await probe(SecurityAction.AUTH, Identity(ip="1.2.3.4"), limiter=limiter)

    assert verdict.is_limited
    assert verdict.retry_after == 37
    assert verdict.headers["Retry-After"] == "37"
    assert verdict.headers["X-RateLimit-Remaining"] == "0"


async def test_budget_unknown_name_fails_loud():
    """An unconfigured action is a caller typo — never a silent default."""
    with pytest.raises(KeyError):
        await probe("nonexistent_budget", Identity(user_id="x"), limiter=_make_limiter())


async def test_budget_uses_configured_engine_algorithm():
    """Budgets run the shared algorithm selector (GCRA/fixed-window),
    not a module-local fixed-window shortcut like the old per-channel limiters."""
    limiter = _make_limiter()

    await probe(SecurityAction.AI_CHAT, Identity(user_id="7"), limiter=limiter)

    method = limiter.check_rate_limit_algorithm
    assert method.await_count == 1  # single engine call, no hand-rolled scripts


async def test_refresh_budget_is_rate_limited_at_configured_values():
    """C1 regression: the refresh edge budget exists and carries Retry-After."""
    limiter = _make_limiter(is_limited=True, remaining=0, retry_after=42)

    verdict = await probe(SecurityAction.REFRESH, Identity(ip="9.9.9.9"), limiter=limiter)

    assert verdict.is_limited
    assert verdict.limit == 60  # refresh budget: 60/min (widened for shared-NAT safety)
    assert verdict.period == 60
    assert verdict.headers["Retry-After"] == "42"


async def test_fail_closed_budget_blocks_on_outage_despite_fail_open():
    """C2 regression: fail-closed budgets 429 on Redis outage even with
    RATE_LIMIT_FAIL_OPEN=True; general policy still fails open."""
    from src.app.core.utils.rate_limit import RateLimiter

    real = RateLimiter()
    # The operator opted into fail-open for general API traffic...
    with (
        patch(
            "src.app.core.config.settings.rate_limit.RATE_LIMIT_FAIL_OPEN",
            True,
        ),
        # ...and Redis is down, which the limiter reports as an outage.
        patch.object(RateLimiter, "get_client", side_effect=Exception("redis down")),
    ):
        verdict = await probe(SecurityAction.REFRESH, Identity(ip="9.9.9.9"), limiter=real)

    assert verdict.is_limited
    assert verdict.remaining == 0
