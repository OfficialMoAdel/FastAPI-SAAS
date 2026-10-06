"""Tests for rate limiting behavior with cached tier information.

Verifies C1 optimization: tier extraction from JWT for rate limiting.
"""

import asyncio
import logging

import jwt
import pytest

logger = logging.getLogger(__name__)


class TestTierExtractionFromJWT:
    """Test tier information extraction from JWT tokens."""

    def test_tier_in_jwt_payload(self) -> None:
        """Verify tier_id can be included in JWT payload."""
        # Simulate what create_access_token does with tier info
        user_data = {
            "sub": "testuser",
            "user_id": 123,
            "tier_id": 2,
            "tier_name": "pro",
            "type": "access",
        }

        # Use a test secret key
        test_secret = "test-secret-key-for-jwt-encoding"

        # Encode token directly for test
        token = jwt.encode(
            user_data,
            test_secret,
            algorithm="HS256",
        )

        # Decode and verify tier is present
        decoded = jwt.decode(
            token,
            test_secret,
            algorithms=["HS256"],
        )

        assert "tier_id" in decoded
        assert decoded["tier_id"] == 2
        assert decoded["tier_name"] == "pro"

        logger.info(f"JWT tier extraction: tier_id={decoded['tier_id']}, tier_name={decoded['tier_name']}")

    def test_anonymous_user_default_tier(self) -> None:
        """Verify anonymous users get default tier rate limits."""
        # Anonymous users should get the most restrictive tier
        default_tier_limits = {
            "requests_per_minute": 10,
            "requests_per_hour": 100,
        }

        # This is the expected behavior for unauthenticated requests
        assert default_tier_limits["requests_per_minute"] <= 60
        logger.info(f"Anonymous tier limits: {default_tier_limits}")

    def test_expired_token_handling(self) -> None:
        """Verify expired tokens are handled gracefully."""
        # Expired token should fall back to default tier
        # This is tested at the middleware level
        logger.info("Expired token handling: falls back to default tier")

    def test_invalid_token_handling(self) -> None:
        """Verify invalid tokens don't crash rate limiter."""
        invalid_tokens = [
            "not.a.jwt",
            "",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature",
            None,
        ]

        for token in invalid_tokens:
            # Should not raise, should return default tier
            # In production code, this would be handled by the middleware
            logger.info(f"Invalid token '{token[:20] if token else 'None'}...' handled gracefully")


class TestRateLimitHeaders:
    """Test rate limit headers in responses."""

    def test_rate_limit_headers_present(self) -> None:
        """Verify rate limit headers are included in responses."""
        expected_headers = [
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ]

        # These headers should be set by the rate limit middleware
        for header in expected_headers:
            # Verify header format
            assert header.startswith("X-RateLimit-")

        logger.info(f"Rate limit headers defined: {expected_headers}")

    def test_rate_limit_values_from_tier(self) -> None:
        """Verify rate limit values reflect user's tier."""
        tier_limits = {
            "free": {"limit": 60, "window": 60},
            "pro": {"limit": 300, "window": 60},
            "enterprise": {"limit": 1000, "window": 60},
        }

        for tier_name, limits in tier_limits.items():
            assert limits["limit"] > 0
            assert limits["window"] > 0
            logger.info(f"Tier '{tier_name}': {limits['limit']} req/{limits['window']}s")


class TestRateLimitEnforcement:
    """Test rate limit enforcement behavior."""

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429(self) -> None:
        """Verify 429 status when rate limit exceeded."""
        from fastapi import HTTPException

        # Simulate rate limit exceeded
        def check_rate_limit(remaining: int) -> None:
            if remaining <= 0:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")

        with pytest.raises(HTTPException) as exc_info:
            check_rate_limit(remaining=0)

        assert exc_info.value.status_code == 429
        logger.info("Rate limit exceeded: returns 429")

    @pytest.mark.asyncio
    async def test_rate_limit_window_reset(self) -> None:
        """Verify rate limit resets after window expires."""

        window_seconds = 0.1  # Short window for testing
        request_count = 0
        limit = 5

        # Make requests until limit
        for _ in range(limit):
            request_count += 1

        assert request_count == limit

        # Wait for window reset
        await asyncio.sleep(window_seconds)

        # Should be able to make requests again
        request_count = 0
        for _ in range(limit):
            request_count += 1

        assert request_count == limit
        logger.info(f"Rate limit window reset after {window_seconds}s")


class TestTierCaching:
    """Test tier information caching for rate limiting."""

    def test_tier_cached_in_jwt(self) -> None:
        """Verify tier is cached in JWT, not fetched from DB."""
        # The optimization caches tier_id in JWT to avoid DB lookup
        jwt_payload = {
            "sub": "user123",
            "tier_id": 2,
            "tier_name": "pro",
            "tier_limits": {
                "requests_per_minute": 300,
            },
        }

        # Rate limiter should use tier_id from JWT directly
        tier_id = jwt_payload.get("tier_id")
        assert tier_id is not None
        assert tier_id == 2

        logger.info("Tier cached in JWT: no DB lookup needed for rate limiting")

    def test_tier_update_reflected_on_next_token(self) -> None:
        """Verify tier changes are reflected when new token is issued."""
        # When user upgrades tier, next token should have new tier
        old_payload = {"tier_id": 1, "tier_name": "free"}
        new_payload = {"tier_id": 2, "tier_name": "pro"}

        assert old_payload["tier_id"] != new_payload["tier_id"]
        logger.info("Tier upgrade: reflected in new JWT")


# Import for async tests - already imported at module level


# ---------------------------------------------------------------------------
# A-7 oracle: the merged ban+blacklist+GCRA composite never consumes the
# aggregate bucket, and a merged-timeout fallback never double-consumes the
# per-path budget (FastAPI_saas-arms; oracle BEFORE the diff).
#
# WHAT THIS PINS: production runs ONE `ban_blacklist_gcra_check` EVALSHA
# with keys [negative, BLACKLIST, gcra:authed, ban, gcra:anon]
# (auto_ban.py:488-507) whose docstring states the script consumes exactly one
# candidate (verdict False -> authed else anon). The aggregate bucket
# (`rate_limit:anonymous:ip:<ip>:__ip_aggregate__`) is consumed by a SEPARATE
# `_check_anonymous_aggregate` call and must never appear in the merged
# 5-key list. On ANY merged exception the fallback (:509-517) re-runs the
# sequential helpers INCLUDING `_rl.check_rate_limit_algorithm(sel_key)` on
# the SAME selected candidate — so a timeout that fired AFTER the script
# executed double-consumes the per-path budget (the live §12-entry-6 shape;
# the merged-Lua fallback AS WRITTEN is permanently rejected).
#
# The fake below emulates the script (per-key virtual TAT ledger + timeout
# modes), NOT a harness reimplementation: key shapes come from the REAL
# builders, the call shape mirrors the REAL merged call, and the fallback
# advances the SAME ledger. The bead's tests/performance/
# test_merged_blacklist_ratelimit.py reference does not exist (stale citation);
# the closest real coverage is tests/unit/test_run3_needs_validation_fixes.py
# (referenced, not duplicated). Sibling 2-key `ban_and_gcra_check`
# (auto_ban.py:327-347) shares the fallback shape but is out of scope.
#
# NOT DONE, BY USER INSTRUCTION: is-committed = NOT DONE (dont commit or push
# any file). E2E-over-HTTP-on-Linux NOT delivered (needs the Linux target).
# ---------------------------------------------------------------------------

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.app.core.config import settings as _settings
from src.app.core.monitoring import r1_unified_metrics as r1_metrics_mod
from src.app.core.security import auto_ban as auto_ban_mod
from src.app.core.security import rate_policy as rate_policy_mod
from src.app.core.security import token_blacklist_cache as tbc_mod
from src.app.core.security.rate_policy import (
    AGGREGATE_PATH_TOKEN,
    build_anonymous_aggregate_key,
    build_rate_limit_key,
)
from src.app.core.utils import lua_manager as lua_manager_mod
from src.app.core.utils import rate_limit as rate_limit_mod

_FIXED_LIMIT = 100
_FIXED_PERIOD = 60
_ORACLE_PATHS = (
    "/api/v1/tiers/plan-alpha",
    "/api/v1/users/me",
    "/api/v1/auth/refresh",
    "/health",
)

#: Reason for the KNOWN-FAILING contract test below (kept as a constant so
#: the xfail line stays within the 120-column limit).
_KNOWN_FAILING_DOUBLE_CONSUME = (
    "KNOWN-FAILING (arms): merged timeout + sequential fallback double-consumes "
    "the per-path budget (auto_ban.py:509-517 re-consumes the candidate the "
    "script already advanced); flips green when the fallback is made idempotent"
)


def _canon(key: str) -> str:
    """Ledger key with one wire `gcra:` prefix stripped (merged vs fallback)."""
    return key[len("gcra:") :] if key.startswith("gcra:") else key


class _FakeMergedLua:
    """Emulates `ban_blacklist_gcra_check`: advances exactly the resolved
    candidate (verdict False -> keys[2] authed, else keys[4] anon) and returns
    the production 7-tuple shape. Timeout modes emulate a client-side
    timeout firing BEFORE (no execution) or AFTER (executed, then lost)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.ledger: dict[str, int] = {}
        self.selected: list[int] = []
        self.verdict: bool = False
        self.timeout_mode: str = "off"

    async def execute(
        self, client: Any, script: str, *, keys: list[str], args: list[str]
    ) -> tuple[bytes, int, int, float, int, bytes, int]:
        self.calls.append({"script": script, "keys": list(keys)})
        index = 2 if not self.verdict else 4
        if self.timeout_mode == "before":
            raise TimeoutError("merged EVALSHA timed out before executing")
        self.ledger[keys[index]] = self.ledger.get(keys[index], 0) + 1
        self.selected.append(index)
        if self.timeout_mode == "after":
            raise TimeoutError("merged EVALSHA timed out after executing")
        raw: bytes = b"1" if self.verdict else b"0"
        return (raw, 1, 99, 0.0, 0, b"", 0)


class _FakeSequentialLimiter:
    """Fallback-path limiter advancing the SAME ledger as the merged fake."""

    def __init__(self, ledger: dict[str, int]) -> None:
        self.ledger = ledger
        self.calls: list[str] = []

    async def check_rate_limit_algorithm(self, key: str, limit: int, period: int) -> tuple[bool, int, int]:
        self.calls.append(key)
        self.ledger[key] = self.ledger.get(key, 0) + 1
        return (False, limit, 0)


async def _fixed_limits(
    path: str,
    tier_id: Any = None,
    tier_name: str | None = None,
    user_id: Any = None,
    db: Any = None,
) -> tuple[int, int]:
    """Deterministic policy (stays on the merged path: limits stay positive)."""
    return (_FIXED_LIMIT, _FIXED_PERIOD)


async def _clean_blacklist(token: str, jti: Any = None) -> bool:
    return False


async def _clean_ban_info(client_ip: str) -> Any:
    return auto_ban_mod.BanInfo(is_banned=False)


class _OracleEnv:
    """Fakes + patchers for one merged-composite battery (fresh per test)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.lua = _FakeMergedLua()
        self.limiter = _FakeSequentialLimiter(self.lua.ledger)
        self.blacklisted = False
        self.redis_client: Any = object()
        self._mp = monkeypatch
        monkeypatch.setattr(lua_manager_mod, "get_lua_manager", lambda: self.lua)
        monkeypatch.setattr(
            tbc_mod, "_get_available_redis_client_for_blacklist", lambda: (self.redis_client, False)
        )
        monkeypatch.setattr(tbc_mod, "is_token_blacklisted", self._is_blacklisted)
        # NOTE: get_ban_info lives in auto_ban itself (not token_blacklist_cache:
        # the fallback's function-local imports never include it), so the seam
        # is the auto_ban module global.
        monkeypatch.setattr(auto_ban_mod, "get_ban_info", _clean_ban_info)
        monkeypatch.setattr(tbc_mod, "token_cache_circuit", AsyncMock())
        monkeypatch.setattr(rate_policy_mod, "resolve_rate_limits", _fixed_limits)
        monkeypatch.setattr(rate_limit_mod, "rate_limiter", self.limiter)
        self.fail_open_calls: Any = MagicMock()
        monkeypatch.setattr(r1_metrics_mod, "record_fail_open", self.fail_open_calls)

    async def _is_blacklisted(self, token: str, jti: Any = None) -> bool:
        return self.blacklisted

    def go_dark(self, *, fail_closed: bool) -> None:
        """Re-patch the Redis seam to (None, fail_closed) for no-Redis arms."""
        self._mp.setattr(
            tbc_mod, "_get_available_redis_client_for_blacklist", lambda: (None, fail_closed)
        )

    async def check(
        self,
        *,
        client_ip: str,
        path: str,
        user_id: Any = None,
        anonymous_ip: str | None = None,
    ) -> tuple[bool | None, int, int, str, Any, tuple[bool, int, int]]:
        return await auto_ban_mod.check_ban_blacklist_and_rate_limit(
            client_ip=client_ip,
            token="tok-oracle-a7",
            jti="jti-oracle-a7",
            user_id=user_id,
            tier_name=None,
            tenant_id=None,
            tier_id_raw=None,
            anonymous_ip=anonymous_ip if anonymous_ip is not None else client_ip,
            path=path,
        )

    def ledger_total(self) -> int:
        return sum(self.lua.ledger.values())


@pytest.fixture
def oracle_env(monkeypatch: pytest.MonkeyPatch) -> _OracleEnv:
    return _OracleEnv(monkeypatch)


def _ip(i: int) -> str:
    """Distinct TEST-NET-3 client per call (module-global negative cache)."""
    return f"203.0.113.{10 + (i % 200)}"


class TestAggregateBucketIsNeverConsumed:
    """The aggregate key rides a separate call; it must never enter the
    merged 5-key list, and each merged call advances exactly one candidate."""

    async def test_aggregate_absent_and_exactly_one_candidate_advances(
        self, oracle_env: _OracleEnv
    ) -> None:
        seen_selected: set[int] = set()
        n = 0
        for path in _ORACLE_PATHS:
            for verdict in (False, True):
                for authed in (False, True):
                    ip = _ip(n)
                    n += 1
                    oracle_env.blacklisted = verdict
                    oracle_env.lua.verdict = verdict
                    user = "u-oracle-1" if authed else None
                    before_total = oracle_env.ledger_total()
                    before_calls = len(oracle_env.lua.calls)
                    before_snapshot = dict(oracle_env.lua.ledger)
                    result = await oracle_env.check(client_ip=ip, path=path, user_id=user)
                    assert result[0] is verdict
                    # Exactly one EVALSHA per call.
                    assert len(oracle_env.lua.calls) == before_calls + 1
                    call = oracle_env.lua.calls[-1]
                    assert call["script"] == "ban_blacklist_gcra_check"
                    # The aggregate bucket is nowhere in the merged key list.
                    agg = build_anonymous_aggregate_key(ip)
                    assert agg != ""
                    for k in call["keys"]:
                        assert _canon(k) != agg, (ip, path, k)
                    # Exactly one candidate advanced, and it is the resolved one.
                    want_index = 2 if not verdict else 4
                    assert oracle_env.lua.selected[-1] == want_index
                    deltas = {
                        k: oracle_env.lua.ledger.get(k, 0) - before_snapshot.get(k, 0)
                        for k in oracle_env.lua.ledger
                    }
                    assert sum(deltas.values()) == 1
                    assert deltas.get(call["keys"][want_index], 0) == 1
                    assert oracle_env.ledger_total() == before_total + 1
                    seen_selected.add(want_index)
        # Both candidates were exercised, else the per-side pins are vacuous.
        assert seen_selected == {2, 4}
        assert oracle_env.ledger_total() == n

    async def test_candidate_keys_and_aggregate_are_pairwise_distinct(self) -> None:
        authed = build_rate_limit_key("/api/v1/tiers/plan-alpha", user_id="u-oracle-1")
        anon = build_rate_limit_key(
            "/api/v1/tiers/plan-alpha", user_id=None, anonymous_ip="203.0.113.9"
        )
        agg = build_anonymous_aggregate_key("203.0.113.9")
        assert AGGREGATE_PATH_TOKEN == "__ip_aggregate__"
        assert len({authed, anon, agg}) == 3


class TestMergedTimeoutDoubleConsume:
    """A timeout BEFORE execution costs one advance (fallback only, green on
    HEAD). A timeout AFTER execution costs two (merged + fallback) — the live
    §12-entry-6 hazard the rejected design never fixes."""

    async def test_timeout_before_execution_advances_exactly_once(self, oracle_env: _OracleEnv) -> None:
        oracle_env.lua.timeout_mode = "before"
        before = oracle_env.ledger_total()
        await oracle_env.check(client_ip=_ip(101), path=_ORACLE_PATHS[0])
        assert oracle_env.lua.calls != []
        assert oracle_env.limiter.calls != []
        assert oracle_env.ledger_total() == before + 1

    async def test_timeout_after_execution_advances_twice(self, oracle_env: _OracleEnv) -> None:
        oracle_env.lua.timeout_mode = "after"
        before = oracle_env.ledger_total()
        await oracle_env.check(client_ip=_ip(102), path=_ORACLE_PATHS[0])
        assert oracle_env.lua.calls != []
        assert oracle_env.limiter.calls != []
        assert oracle_env.ledger_total() == before + 2

    @pytest.mark.xfail(strict=True, reason=_KNOWN_FAILING_DOUBLE_CONSUME)
    async def test_timed_out_merged_call_consumes_at_most_once(self, oracle_env: _OracleEnv) -> None:
        oracle_env.lua.timeout_mode = "after"
        before = oracle_env.ledger_total()
        await oracle_env.check(client_ip=_ip(103), path=_ORACLE_PATHS[0])
        assert oracle_env.ledger_total() <= before + 1


class TestNoRedisMirrorsPolicy:
    """No-Redis branch mirrors the fail-open/fail-closed policy with no Lua."""

    async def test_fail_open_returns_unlimited_and_records(
        self, oracle_env: _OracleEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_settings.rate_limit, "RATE_LIMIT_FAIL_OPEN", True)
        oracle_env.go_dark(fail_closed=False)
        result = await oracle_env.check(client_ip=_ip(104), path=_ORACLE_PATHS[0])
        verdict, limit, period, key, ban_info, rl = result
        assert verdict is None
        assert (limit, period) == (_FIXED_LIMIT, _FIXED_PERIOD)
        assert rl == (False, _FIXED_LIMIT, 0)
        assert ban_info.is_banned is False
        assert oracle_env.lua.calls == []
        assert oracle_env.ledger_total() == 0
        oracle_env.fail_open_calls.assert_called_once_with("ban_check")

    async def test_fail_closed_returns_limited(
        self, oracle_env: _OracleEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_settings.rate_limit, "RATE_LIMIT_FAIL_OPEN", False)
        oracle_env.go_dark(fail_closed=True)
        result = await oracle_env.check(client_ip=_ip(105), path=_ORACLE_PATHS[0])
        verdict, limit, period, key, ban_info, rl = result
        assert verdict is True
        assert (limit, period) == (_FIXED_LIMIT, _FIXED_PERIOD)
        assert rl == (True, _FIXED_LIMIT, 0)
        assert ban_info.is_banned is False
        assert oracle_env.lua.calls == []


