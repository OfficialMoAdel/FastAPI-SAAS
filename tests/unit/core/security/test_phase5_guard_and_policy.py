"""Phase 5: the ADR-0015 idempotency guard, the RLS log policy, and the sweep.

WHAT THIS PINS
    §8.3  the merged GCRA composites carry an ADR-0015 idempotency guard: the
          script claims a per-REQUEST nonce BEFORE it moves any budget, so a
          timeout that fired AFTER execution cannot make the Python fallback
          consume the same budget twice. The fallback fails CLOSED (429) in
          that case -- the ADR's sanctioned direction -- and only takes the
          sequential path when the nonce proves the script never ran.
    F-14  the no-tenant RLS warning is DEBUG, as an explicit policy decision
          (it is NOT claimed to be isomorphic).
    §6    `uses_gcra`, `math`, `build_tenant_key` and the header scan location
          are computed once instead of per call.

WHY THE NONCE LIVES IN THE SCRIPT
    The ambiguity it resolves is "did the SERVER execute it?", which only the
    server can answer. A Python-side marker set before the call cannot
    distinguish "never ran" from "ran, reply lost"; a marker set after cannot
    be reached when the call raised. Claiming it as the script's FIRST
    operation is the only point that makes the two cases distinguishable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from src.app.core.security import auto_ban as auto_ban_mod
from src.app.core.utils.lua_manager import LuaScriptManager

_REPO = Path(__file__).resolve().parents[4]


class TestTheGuardIsInTheScript:
    @pytest.mark.parametrize("script", ["ban_and_gcra_check", "ban_blacklist_gcra_check"])
    def test_the_nonce_is_claimed_before_any_budget_moves(self, script: str) -> None:
        source = LuaScriptManager.BUILTIN_SCRIPTS[script]
        assert "__nonce_key" in source, f"{script} must carry the ADR-0015 nonce"
        nonce_at = source.index("__nonce_key")
        # The claim must precede the first budget mutation (SET of a TAT key).
        first_set = source.index("redis.call('SET', __GCRA_KEY__" if "__GCRA_KEY__" in source else "new_tat_ms")
        assert nonce_at < first_set, f"{script}: the nonce must be claimed BEFORE the budget moves"

    @pytest.mark.parametrize("script", ["ban_and_gcra_check", "ban_blacklist_gcra_check"])
    def test_a_replay_returns_the_duplicate_marker(self, script: str) -> None:
        source = LuaScriptManager.BUILTIN_SCRIPTS[script]
        assert "return {'duplicate'}" in source

    def test_the_claim_is_conditional_and_ttl_bounded(self) -> None:
        """NX + PX: first execution wins, and the key cannot outlive its request."""
        for script in ("ban_and_gcra_check", "ban_blacklist_gcra_check"):
            source = LuaScriptManager.BUILTIN_SCRIPTS[script]
            assert "'NX'" in source and "'PX'" in source


class TestTheFallbackFailsClosedOnlyWhenProven:
    @pytest.mark.asyncio
    async def test_a_claimed_nonce_fails_closed(self) -> None:
        """The script ran, so re-running would charge the caller twice."""
        redis = MagicMock()
        redis.exists = AsyncMock(return_value=1)
        with (
            patch.object(auto_ban_mod, "get_redis", return_value=redis),
            patch("src.app.core.utils.lua_manager.get_lua_manager") as lua,
            patch.object(auto_ban_mod, "get_ban_info", AsyncMock()) as get_ban,
        ):
            lua.return_value.execute = AsyncMock(side_effect=TimeoutError("timeout after execution"))
            _ban, rl, _agg = await auto_ban_mod.check_ban_and_rate_limit(
                "1.2.3.4", "rate_limit:anonymous:ip:1.2.3.4:p", limit=10, period=60
            )
        assert rl[0] is True, "an ambiguous timeout must fail CLOSED"
        assert rl[3] is True, "the verdict is marked degraded"
        get_ban.assert_not_awaited(), "the sequential path must not run"

    @pytest.mark.asyncio
    async def test_an_unclaimed_nonce_takes_the_sequential_path(self) -> None:
        """The script provably never ran, so one sequential advance is correct."""
        redis = MagicMock()
        redis.exists = AsyncMock(return_value=0)
        limiter = MagicMock()
        limiter.check_rate_limit_algorithm_with_status = AsyncMock(return_value=(False, 9, 0, False))
        with (
            patch.object(auto_ban_mod, "get_redis", return_value=redis),
            patch("src.app.core.utils.lua_manager.get_lua_manager") as lua,
            patch.object(auto_ban_mod, "get_ban_info", AsyncMock(return_value=auto_ban_mod.BanInfo(False))),
            patch("src.app.core.utils.rate_limit.rate_limiter", limiter),
        ):
            lua.return_value.execute = AsyncMock(side_effect=ConnectionError("never executed"))
            _ban, rl, _agg = await auto_ban_mod.check_ban_and_rate_limit(
                "1.2.3.4", "rate_limit:anonymous:ip:1.2.3.4:p", limit=10, period=60
            )
        assert rl == (False, 9, 0, False)
        limiter.check_rate_limit_algorithm_with_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_non_int_probe_is_not_evidence(self) -> None:
        """A mock/malformed answer must not be read as 'already executed'."""
        redis = MagicMock()
        redis.exists = AsyncMock(return_value=MagicMock())  # truthy, not an int
        assert await auto_ban_mod._nonce_was_claimed(redis, "n") is False

    @pytest.mark.asyncio
    async def test_a_failing_probe_is_not_evidence_either(self) -> None:
        # The probe is best-effort on ANY Redis failure: a blip must not be
        # read as "the script already ran", which would fail closed on a
        # request that never executed anything.
        redis = MagicMock()
        redis.exists = AsyncMock(side_effect=RedisError("redis down"))
        assert await auto_ban_mod._nonce_was_claimed(redis, "n") is False

    @pytest.mark.asyncio
    async def test_a_non_redis_failure_is_not_swallowed(self) -> None:
        # The handler is narrowed to RedisError: a programming error in the
        # probe (or a bad key type) must still surface, because a swallowed
        # bug here would silently take the sequential path on every request.
        redis = MagicMock()
        redis.exists = AsyncMock(side_effect=TypeError("bad key type"))
        with pytest.raises(TypeError):
            await auto_ban_mod._nonce_was_claimed(redis, "n")

    def test_each_request_gets_a_fresh_nonce(self) -> None:
        a = auto_ban_mod._new_request_nonce()
        b = auto_ban_mod._new_request_nonce()
        assert a != b and len(a) == 32
        assert auto_ban_mod._nonce_key(a).startswith("ratelimit:nonce:")


class TestTheDuplicateReplayDoesNotConsume:
    """The Lua guard replies ``'duplicate'`` -- as the production client decodes it.

    The production Redis client is ``decode_responses=True``
    (``redis_provider``), so the sentinel arrives as ``str``. These cases mock
    that shape: the fast path is only reachable in production if the str form
    is accepted, and a mock that pins ``bytes`` alone lets the branch rot.
    """

    @pytest.mark.asyncio
    async def test_a_duplicate_result_reports_allowed(self) -> None:
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        with (
            patch.object(auto_ban_mod, "get_redis", return_value=redis),
            patch("src.app.core.utils.lua_manager.get_lua_manager") as lua,
        ):
            lua.return_value.execute = AsyncMock(return_value=["duplicate"])
            ban, rl, _agg = await auto_ban_mod.check_ban_and_rate_limit(
                "1.2.3.4", "rate_limit:anonymous:ip:1.2.3.4:p", limit=10, period=60
            )
        assert rl[0] is False, "a replay must not report a fresh rejection"
        assert ban.is_banned is False

    @pytest.mark.asyncio
    async def test_a_duplicate_replay_still_honours_a_ban(self) -> None:
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"spam")
        redis.ttl = AsyncMock(return_value=120)
        with (
            patch.object(auto_ban_mod, "get_redis", return_value=redis),
            patch("src.app.core.utils.lua_manager.get_lua_manager") as lua,
        ):
            lua.return_value.execute = AsyncMock(return_value=["duplicate"])
            ban, _rl, _agg = await auto_ban_mod.check_ban_and_rate_limit(
                "1.2.3.4", "rate_limit:anonymous:ip:1.2.3.4:p", limit=10, period=60
            )
        assert ban.is_banned is True and ban.reason == "spam"

    @pytest.mark.asyncio
    async def test_the_undecoded_bytes_sentinel_is_also_accepted(self) -> None:
        """``bytes`` stays accepted: the reply shape depends on the client.

        Accepting only ``str`` would break any non-decoding client (and the
        ADR's own undecoded deployments). Both forms route to the same
        fast-path branch.
        """
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        with (
            patch.object(auto_ban_mod, "get_redis", return_value=redis),
            patch("src.app.core.utils.lua_manager.get_lua_manager") as lua,
        ):
            lua.return_value.execute = AsyncMock(return_value=[b"duplicate"])
            ban, rl, _agg = await auto_ban_mod.check_ban_and_rate_limit(
                "1.2.3.4", "rate_limit:anonymous:ip:1.2.3.4:p", limit=10, period=60
            )
        assert rl[0] is False, "a replay must not report a fresh rejection"
        assert ban.is_banned is False


class TestF14RlsLogPolicy:
    def test_the_no_tenant_record_is_debug(self) -> None:
        source = (_REPO / "src/app/core/db/rls.py").read_text(encoding="utf-8")
        marker = "no tenant context at pool checkout"
        index = source.index(marker)
        # The call immediately preceding the message must be logger.debug.
        preceding = source[:index].rsplit("logger.", 1)[-1]
        assert preceding.startswith("debug("), "F-14: this record is a DEBUG policy decision"

    def test_the_security_failure_path_is_still_critical(self) -> None:
        source = (_REPO / "src/app/core/db/rls.py").read_text(encoding="utf-8")
        assert "logger.critical(" in source, "a failed restore must still be loud"


class TestTheSweepIsInPlace:
    def test_uses_gcra_is_memoized_but_still_honours_a_runtime_change(self) -> None:
        """§6: no `.lower()` per call -- but the engine fence stays live.

        A cache frozen at import would break the contract the tests in
        `test_rate_policy.py` and `test_gcra_degradation_metric.py` pin: a
        fixed-window operator must not be routed through the GCRA composite.
        """
        from src.app.core.config import settings
        from src.app.core.security import rate_policy

        rate_policy._GCRA_DECISION_CACHE.clear()
        original = settings.rate_limit.RATE_LIMIT_ALGORITHM
        try:
            settings.rate_limit.RATE_LIMIT_ALGORITHM = "gcra"
            assert rate_policy.uses_gcra() is True
            assert rate_policy._GCRA_DECISION_CACHE["gcra"] is True
            settings.rate_limit.RATE_LIMIT_ALGORITHM = "fixed_window"
            assert rate_policy.uses_gcra() is False, "the fence must follow the setting"
        finally:
            settings.rate_limit.RATE_LIMIT_ALGORITHM = original
            rate_policy._GCRA_DECISION_CACHE.clear()

    def test_math_and_build_tenant_key_are_module_level(self) -> None:
        source = (_REPO / "src/app/core/security/rate_policy.py").read_text(encoding="utf-8")
        assert "\nimport math\n" in source
        assert "\nfrom src.app.core.utils.cache import build_tenant_key\n" in source
        assert "    from src.app.core.utils.cache import build_tenant_key" not in source
        assert "                import math" not in source

    def test_header_locations_are_precomputed(self) -> None:
        from src.app.middleware import attack_detection as ad

        assert ad._HEADER_LOCATIONS[b"user-agent"] == "header:user-agent"
        assert set(ad._HEADER_LOCATIONS) == ad._HEADERS_TO_CHECK_BYTES


async def _noop_app(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
    """An app that does nothing — the flags are read before it is called."""
    return None


async def _receive() -> dict:  # type: ignore[type-arg]
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(message) -> None:  # type: ignore[no-untyped-def]
    return None


class TestTheKillSwitchesStayReachable:
    def test_the_per_request_settings_read_is_deliberate(self) -> None:
        """The sweep must NOT hoist these: they are incident kill switches.

        `__call__` re-reads the four attack-detection settings each request so
        an operator can flip them without a deploy. Hoisting them to __init__
        would silently turn a live switch into a restart-only one.

        Driven through the real middleware: the four flags are refreshed
        at the top of `__call__`, so driving a request through the instance
        is what proves a flip is live. A hoisted copy would keep answering
        with the value captured at `__init__`.
        """
        from src.app.core.config import settings
        from src.app.middleware.attack_detection import AttackDetectionMiddleware

        original = settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE
        middleware = AttackDetectionMiddleware(app=_noop_app)

        async def drive() -> bool:
            await middleware(
                {"type": "http", "path": "/health", "method": "GET", "headers": []},
                _receive,
                _send,
            )
            return middleware._branch_prune

        try:
            settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE = True
            assert asyncio.run(drive()) is True
            settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE = False
            assert asyncio.run(drive()) is False, "a live flip must be visible to the next request"
        finally:
            settings.attack_detection.ATTACK_DETECTION_BRANCH_PRUNE = original
            middleware._branch_prune = original

    @pytest.mark.parametrize("error", [ConnectionError("conn refused"), OSError("socket gone")])
    @pytest.mark.asyncio
    async def test_the_transport_class_is_not_a_redis_error(self, error) -> None:
        """A Redis outage is not always a RedisError.

        ``redis.exceptions.ConnectionError`` IS a ``RedisError``, but the
        builtin ``ConnectionError`` (an ``OSError``) is what a replaced
        client or a socket dying mid-call raises. The probe is best-effort
        against both: neither is evidence that the merged script ran.
        """
        redis = MagicMock()
        redis.exists = AsyncMock(side_effect=error)
        assert await auto_ban_mod._nonce_was_claimed(redis, "n") is False
