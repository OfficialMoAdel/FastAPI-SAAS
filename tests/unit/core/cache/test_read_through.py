"""The read-through seam: five decisions, one implementation.

``CacheClient`` offered primitives only, so every caller that wanted "return the
cached value, or compute it once" wrote the choreography itself — five times,
with three give-up budgets and two jitter expressions. This module pins the
consolidated behaviour, including the two defects the consolidation fixes:

1. ``stampede.get_with_lock`` used ONE number (``lock_timeout``) for both the
   claim's TTL and the waiter's patience, so a waiter gave up at exactly the
   moment the claim became claimable by someone else.
2. Nothing could express "I computed a value that must NOT be published"
   (ADR-0029: the feature-flag service returns the CALLER's default, and caching
   it would serve one caller's fallback to every tenant for the whole TTL).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.app.core.cache.read_through import ReadThroughPolicy, read_through


def _cache(*, client: Any = object(), gets: list[Any] | None = None) -> MagicMock:
    """A CacheClient double exposing exactly the primitives the seam uses.

    With ``gets=None`` every read misses (a cold key, for the whole call). Pass
    a list to script a sequence — the winner path reads twice (initial miss,
    then a double-check), so a one-element list would be exhausted.
    """
    cache = MagicMock()
    cache.client = client
    cache.get_json = (
        AsyncMock(side_effect=list(gets)) if gets is not None else AsyncMock(return_value=None)
    )
    cache.set_nx = AsyncMock(return_value=True)
    cache.set_json = AsyncMock()
    cache.release_lock_if_owned = AsyncMock(return_value=True)
    return cache


class TestThePolicyRefusesADeadlyBudgetPair:
    def test_give_up_must_be_shorter_than_the_claim_ttl(self) -> None:
        """A waiter that outlives the claim races the next claimer.

        This is the bug the two numbers exist to separate, so it fails at
        construction rather than producing duplicate work at runtime.
        """
        with pytest.raises(ValueError, match="give_up_seconds must be <"):
            ReadThroughPolicy(give_up_seconds=10.0, lock_ttl_seconds=10)

    def test_equal_budgets_are_rejected_too(self) -> None:
        with pytest.raises(ValueError, match="give_up_seconds must be <"):
            ReadThroughPolicy(give_up_seconds=5.0, lock_ttl_seconds=5)

    def test_a_negative_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            ReadThroughPolicy(give_up_seconds=-1.0, lock_ttl_seconds=15)

    def test_the_default_policy_is_valid(self) -> None:
        ReadThroughPolicy()  # must not raise

    def test_a_non_distributed_policy_is_not_constrained(self) -> None:
        """With no claim there is nothing to outlive."""
        ReadThroughPolicy(claim="none", give_up_seconds=99.0, lock_ttl_seconds=1)


class TestTheFiveDecisions:
    async def test_decision_1_claim_none_never_touches_the_lock(self) -> None:
        cache = _cache()
        fetch = AsyncMock(return_value={"v": 1})

        result = await read_through(
            cache, "k", fetch, ttl=60, policy=ReadThroughPolicy(claim="none")
        )

        assert result == {"v": 1}
        cache.set_nx.assert_not_awaited()
        cache.set_json.assert_awaited_once(), "compute-and-publish still publishes"

    async def test_decision_1_absent_backend_computes_without_a_claim(self) -> None:
        cache = _cache(client=None)
        fetch = AsyncMock(return_value={"v": 1})

        result = await read_through(cache, "k", fetch, ttl=60)

        assert result == {"v": 1}
        cache.set_nx.assert_not_awaited()

    async def test_decision_2_freshness_ttl_serves_a_hit_without_fetch(self) -> None:
        cache = _cache(gets=[{"cached": True}])
        fetch = AsyncMock()

        assert await read_through(cache, "k", fetch, ttl=60) == {"cached": True}
        fetch.assert_not_awaited()

    async def test_decision_3_give_up_budget_is_bounded(self) -> None:
        """The loser computes once the budget is spent — it does not stall."""
        cache = _cache()  # every read misses
        cache.set_nx = AsyncMock(return_value=False)
        fetch = AsyncMock(return_value={"mine": True})

        # Drive the clock rather than the real one: with ``sleep`` mocked, real
        # time barely advances, so the deadline would be reached only after an
        # unpredictable number of polls.
        ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
        with (
            patch("src.app.core.cache.read_through.asyncio.sleep", new=AsyncMock()),
            patch("src.app.core.cache.read_through._random_uniform", return_value=0.0),
            patch("src.app.core.cache.read_through.time.monotonic", side_effect=lambda: next(ticks)),
        ):
            result = await read_through(
                cache, "k", fetch, ttl=60, policy=ReadThroughPolicy(give_up_seconds=0.01)
            )

        assert result == {"mine": True}
        fetch.assert_awaited_once()

    async def test_decision_4_key_is_taken_verbatim(self) -> None:
        """The seam never rewrites the key.

        Tenant scoping — including the public namespace used when no tenant is
        bound — is ``tenant_key``'s decision, applied by the caller. A seam that
        "helpfully" namespaced would be able to publish one tenant's value into
        a public slot.
        """
        cache = _cache()
        await read_through(cache, "tenant:t1:thing", AsyncMock(return_value=1), ttl=60)

        args = cache.set_json.await_args.args
        assert args[0] == "tenant:t1:thing"

    async def test_decision_5_should_cache_false_returns_without_publishing(self) -> None:
        """ADR-0029's case: the winner computed something unshareable."""
        cache = _cache()
        fetch = AsyncMock(return_value="caller-specific-default")

        result = await read_through(
            cache,
            "k",
            fetch,
            ttl=60,
            policy=ReadThroughPolicy(should_cache=lambda v: v != "caller-specific-default"),
        )

        assert result == "caller-specific-default", "the computing caller still gets its value"
        cache.set_json.assert_not_awaited(), "one caller's fallback must not reach everyone"

    async def test_decision_5_should_cache_true_publishes(self) -> None:
        cache = _cache()
        await read_through(
            cache, "k", AsyncMock(return_value=1), ttl=60, policy=ReadThroughPolicy(should_cache=bool)
        )
        cache.set_json.assert_awaited_once()


class TestTheClaimIsOwnershipChecked:
    async def test_the_winner_releases_with_its_own_token(self) -> None:
        cache = _cache()
        await read_through(cache, "k", AsyncMock(return_value=1), ttl=60)

        args = cache.release_lock_if_owned.await_args.args
        assert args[0] == "k:refresh"
        assert isinstance(args[1], str) and args[1], "the release must carry a token"

    async def test_a_failed_fetch_still_releases(self) -> None:
        """A leaked claim blocks the key for its whole TTL."""
        cache = _cache()
        with pytest.raises(RuntimeError):
            await read_through(cache, "k", AsyncMock(side_effect=RuntimeError("boom")), ttl=60)

        cache.release_lock_if_owned.assert_awaited_once()

    async def test_a_peer_is_never_released(self) -> None:
        cache = _cache(gets=[None, None, {"peer": 1}])
        cache.set_nx = AsyncMock(return_value=False)

        with (
            patch("src.app.core.cache.read_through.asyncio.sleep", new=AsyncMock()),
            patch("src.app.core.cache.read_through._random_uniform", return_value=0.0),
        ):
            await read_through(cache, "k", AsyncMock(), ttl=60)

        cache.release_lock_if_owned.assert_not_awaited(), "deleting another's claim reopens the race"


class TestTransportFailureIsNotAProgrammingError:
    @pytest.mark.parametrize(
        "error",
        [RedisConnectionError("redis down"), ConnectionError("socket gone"), OSError("reset")],
    )
    async def test_a_transport_failure_computes_instead_of_stalling(self, error) -> None:
        """``set_nx`` propagates a transport error; treating it as "peer holds
        the claim" turns a blip into a stall."""
        cache = _cache()
        cache.set_nx = AsyncMock(side_effect=error)
        fetch = AsyncMock(return_value={"v": 1})

        result = await read_through(cache, "k", fetch, ttl=60)

        assert result == {"v": 1}
        fetch.assert_awaited_once()
        cache.release_lock_if_owned.assert_not_awaited()

    async def test_a_programming_error_is_not_swallowed(self) -> None:
        """A TypeError means the call is wrong; hiding it degrades every read."""
        cache = _cache()
        cache.set_nx = AsyncMock(side_effect=TypeError("bad key type"))

        with pytest.raises(TypeError):
            await read_through(cache, "k", AsyncMock(), ttl=60)

    async def test_a_non_bool_claim_verdict_is_normalised(self) -> None:
        """A backend returning a truthy non-bool must not skip publishing."""
        cache = _cache()
        cache.set_nx = AsyncMock(return_value=1)

        await read_through(cache, "k", AsyncMock(return_value={"v": 1}), ttl=60)

        cache.set_json.assert_awaited_once()


class TestMetricsSurviveTheSeam:
    async def test_a_hit_records_a_hit(self) -> None:
        cache = _cache(gets=[{"cached": 1}])
        with patch("src.app.core.monitoring.r1_unified_metrics.record_xfetch_latency") as rec:
            await read_through(cache, "k", AsyncMock(), ttl=60)

        assert rec.call_args.kwargs["is_hit"] is True

    async def test_a_miss_records_a_miss(self) -> None:
        cache = _cache()
        with patch("src.app.core.monitoring.r1_unified_metrics.record_xfetch_latency") as rec:
            await read_through(cache, "k", AsyncMock(return_value=1), ttl=60)

        assert rec.call_args.kwargs["is_hit"] is False

    async def test_a_metrics_failure_never_breaks_the_read(self) -> None:
        cache = _cache()
        with patch(
            "src.app.core.monitoring.r1_unified_metrics.record_xfetch_latency",
            side_effect=RuntimeError("metrics down"),
        ):
            assert await read_through(cache, "k", AsyncMock(return_value={"v": 1}), ttl=60) == {"v": 1}
