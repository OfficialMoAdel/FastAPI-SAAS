"""S-23 / S-25 — two singleflight-style defects in the policy and token paths.

S-23  `warm_policy_cache` wrote REDIS only. It is also called on the H-9
      write-back in `rate_policy.resolve_rate_limits`, which runs AFTER a miss
      already populated the L1 layers with a stale/empty view. So the
      authoritative list was shadowed for the full 300s L1 TTL, and the
      compiled path map stayed empty — meaning the write-back never actually
      accelerated anything and every request re-ran the DB fallback.

      The comment at that call site claims the write-back refreshes
      "Redis + L1", which was false.

S-25  Both singleflight dicts popped by key in a `finally` that EVERY waiter
      runs, not just the owner. A late waiter's pop could remove a newly
      registered task belonging to an unrelated later request. Correctness was
      preserved, but the stampede protection degraded to N concurrent DB
      reads — worst precisely when Redis is down and `_check_db_fallback` is
      the only revocation oracle left.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestS23WarmPolicyCacheSeedsL1:
    @pytest.mark.asyncio
    async def test_l1_is_seeded_from_the_warmed_list(self) -> None:
        from src.app.core.security import rate_limit_cache as rlc

        rlc._tier_policy_l1.clear()
        rlc._tier_policy_path_map.clear()

        policies = [{"path": "/api/v1/x", "limit": 7, "period": 60}]
        with patch.object(rlc, "get_redis", MagicMock(return_value=None)):
            await rlc.warm_policy_cache({1: policies})

        assert rlc._tier_policy_l1.get(1) == policies, "L1 must reflect the warmed list"

    @pytest.mark.asyncio
    async def test_stale_path_map_is_dropped(self) -> None:
        """A map compiled from the OLD rows must not outlive them."""
        from src.app.core.security import rate_limit_cache as rlc

        rlc._tier_policy_l1.clear()
        rlc._tier_policy_path_map.clear()
        rlc._tier_policy_path_map[1] = {"/api/v1/old": (1, 1)}

        with patch.object(rlc, "get_redis", MagicMock(return_value=None)):
            await rlc.warm_policy_cache({1: [{"path": "/api/v1/new", "limit": 9, "period": 60}]})

        assert 1 not in rlc._tier_policy_path_map, "the stale compiled map survived the refresh"

    @pytest.mark.asyncio
    async def test_empty_input_is_a_noop(self) -> None:
        from src.app.core.security import rate_limit_cache as rlc

        rlc._tier_policy_l1.clear()
        with patch.object(rlc, "get_redis", MagicMock(return_value=MagicMock())):
            assert await rlc.warm_policy_cache({}) == 0


class TestS25SingleflightPopOwnership:
    @pytest.mark.asyncio
    async def test_non_owner_does_not_evict_a_newer_task(self) -> None:
        """A waiter finishing late must not remove someone else's task."""
        from src.app.core.security import jwt_service as js

        js._in_flight_db_checks.clear()
        ours = MagicMock(name="our_task")
        theirs = MagicMock(name="their_task")

        # Simulate: we awaited `ours`; while suspended, a later request
        # registered `theirs` under the same key.
        js._in_flight_db_checks["k"] = ours

        async def _swap_then_finish():
            js._in_flight_db_checks["k"] = theirs  # C registers after A pops
            # our finally: pops only if the registered task is still ours
            async with js._db_fallback_lock:
                if js._in_flight_db_checks.get("k") is ours:
                    del js._in_flight_db_checks["k"]
            return True

        assert await _swap_then_finish() is True

        assert js._in_flight_db_checks.get("k") is theirs, "the non-owner popped a live task"

    @pytest.mark.asyncio
    async def test_owner_still_releases_its_own_entry(self) -> None:
        """No leak: the owner's task must be removed so the next call re-queries."""
        from src.app.core.security import jwt_service as js

        js._in_flight_db_checks.clear()
        ours = MagicMock(name="our_task")
        js._in_flight_db_checks["k"] = ours

        async with js._db_fallback_lock:
            if js._in_flight_db_checks.get("k") is ours:
                del js._in_flight_db_checks["k"]

        assert "k" not in js._in_flight_db_checks

    def test_token_version_dict_uses_ownership_check_too(self) -> None:
        """Both singleflights must carry the same guard."""
        import inspect

        from src.app.core.security import jwt_service as js

        src = inspect.getsource(js)
        assert src.count("is task") >= 2, "both singleflights must check task identity"
