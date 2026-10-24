"""The ONE cache-invalidation exhaustion policy, and the four family declarations.

Two layers are pinned here:

1. the shared policy itself — retry once, warn on the first failure, error on
   exhaustion, and write a poison-key marker only when the family DECLARES it;
2. each of the four call sites (tenant, tier, rate-limit, user) actually routes
   through that policy and honours its own declared choice.

The layer-2 tests are the ones that bite: they fail if a call site grows its own
retry loop back, or if a family's marker decision silently flips.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.cache.invalidation_exhaustion import (
    INVALIDATION_ATTEMPTS,
    ExhaustionMarker,
    InvalidationPolicy,
    invalidate_with_exhaustion,
)
from src.app.modules.rate_limits.service import _RATE_LIMIT_INVALIDATION_POLICY
from src.app.modules.tenants.service import _TENANT_INVALIDATION_POLICY
from src.app.modules.tiers.service import _TIER_INVALIDATION_POLICY
from src.app.modules.users.cache import _USER_INVALIDATION_POLICY

_MARKER_POLICY = InvalidationPolicy(
    family="Widget",
    on_exhaustion=ExhaustionMarker.POISON_KEY,
    exhausted_message="Widget cache invalidation failed after retry — stale widget may persist until TTL",
)
_LOG_ONLY_POLICY = InvalidationPolicy(
    family="Gadget",
    on_exhaustion=ExhaustionMarker.LOG_ONLY,
    exhausted_message="Gadget cache invalidation failed after retry — stale gadget may persist until TTL",
)


def _failing(message: str = "redis down") -> AsyncMock:
    call = AsyncMock(side_effect=RuntimeError(message))
    return call


def _client_with_pipeline(execute_side_effect: Any) -> MagicMock:
    """A Redis client whose ONE pipeline delete carries the failure.

    The tenants invalidation batches its keys into a single pipelined delete,
    so the retry budget is spent on ``pipeline.execute()`` — which is the
    thing that actually reaches Redis.
    """

    class _Pipe:
        def __init__(self) -> None:
            self.delete = MagicMock()

        async def __aenter__(self) -> _Pipe:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def execute(self) -> list[object]:
            return await execute_side_effect()

    client = MagicMock()
    client.pipeline = MagicMock(side_effect=lambda transaction=False: _Pipe())
    return client


def _fails_first_call(message: str = "boom") -> AsyncMock:
    state = {"calls": 0}

    async def _side_effect(*args: object, **kwargs: object) -> int:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError(message)
        return 1

    return AsyncMock(side_effect=_side_effect)


# ---------------------------------------------------------------------------
# The shared policy
# ---------------------------------------------------------------------------


class TestExhaustionPolicyRow:
    """The declaration itself: an incomplete row is a bug, not a default."""

    def test_blank_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="family"):
            InvalidationPolicy(
                family="  ",
                on_exhaustion=ExhaustionMarker.LOG_ONLY,
                exhausted_message="something may persist until TTL",
            )

    def test_blank_exhausted_message_rejected(self) -> None:
        with pytest.raises(ValueError, match="exhausted_message"):
            InvalidationPolicy(
                family="Widget",
                on_exhaustion=ExhaustionMarker.LOG_ONLY,
                exhausted_message="   ",
            )

    def test_attempts_are_exactly_one_retry(self) -> None:
        """The retry budget is policy: one attempt plus one retry, nothing else."""
        assert INVALIDATION_ATTEMPTS == (1, 2)

    @pytest.mark.asyncio
    async def test_marker_family_without_writer_fails_loud(self) -> None:
        """A declared marker with no writer would degrade to log-only silently."""

        async def _invalidate() -> None:
            return None

        with pytest.raises(ValueError, match="poison-key marker"):
            await invalidate_with_exhaustion(_invalidate, policy=_MARKER_POLICY, log_context={})


@pytest.mark.asyncio
class TestInvalidateWithExhaustion:
    async def test_success_first_attempt_runs_once(self) -> None:
        invalidate = AsyncMock()
        marker = AsyncMock()

        assert (
            await invalidate_with_exhaustion(invalidate, policy=_MARKER_POLICY, log_context={}, write_marker=marker)
            is True
        )
        assert invalidate.await_count == 1
        marker.assert_not_awaited()

    async def test_first_failure_warns_then_retries(self) -> None:
        invalidate = AsyncMock(side_effect=[RuntimeError("boom"), None])
        marker = AsyncMock()

        assert (
            await invalidate_with_exhaustion(invalidate, policy=_MARKER_POLICY, log_context={}, write_marker=marker)
            is True
        )
        assert invalidate.await_count == 2
        # A recovered retry is not exhaustion: no marker, and no error record.
        marker.assert_not_awaited()

    async def test_exhaustion_writes_marker_and_reports_false(self) -> None:
        invalidate = _failing()
        marker = AsyncMock()

        result = await invalidate_with_exhaustion(
            invalidate,
            policy=_MARKER_POLICY,
            log_context={"widget_id": "w-1"},
            write_marker=marker,
        )

        assert result is False
        assert invalidate.await_count == len(INVALIDATION_ATTEMPTS)
        marker.assert_awaited_once()

    async def test_exhaustion_writes_nothing_for_log_only_family(self) -> None:
        """A log-only family must not grow a marker, even if one is offered."""
        invalidate = _failing()
        marker = AsyncMock()

        result = await invalidate_with_exhaustion(
            invalidate,
            policy=_LOG_ONLY_POLICY,
            log_context={"gadget_id": "g-1"},
            write_marker=marker,
        )

        assert result is False
        marker.assert_not_awaited()

    async def test_marker_failure_does_not_mask_exhaustion(self) -> None:
        """Redis is usually down for BOTH calls; the error log is the record."""
        invalidate = _failing()
        marker = AsyncMock(side_effect=RuntimeError("redis still down"))

        result = await invalidate_with_exhaustion(
            invalidate, policy=_MARKER_POLICY, log_context={}, write_marker=marker
        )

        assert result is False
        marker.assert_awaited_once()

    async def test_exhausted_log_names_the_family_and_context(self) -> None:
        """The stale window must be attributable, not just 'something broke'."""
        from src.app.core.cache import invalidation_exhaustion

        invalidate = _failing("conn reset")
        error = MagicMock()
        warning = MagicMock()

        with (
            patch.object(invalidation_exhaustion, "logger", _recording(error, warning)),
        ):
            await invalidate_with_exhaustion(
                invalidate,
                policy=_LOG_ONLY_POLICY,
                log_context={"gadget_id": "g-9"},
            )

        warning.assert_called_once()
        assert warning.call_args.args[0] == "Gadget cache invalidation failed (retrying)"
        error.assert_called_once()
        assert error.call_args.args[0] == _LOG_ONLY_POLICY.exhausted_message
        assert error.call_args.kwargs["gadget_id"] == "g-9"
        assert "conn reset" in error.call_args.kwargs["error"]


def _recording(error: MagicMock, warning: MagicMock) -> MagicMock:
    """A structlog-shaped logger double: ``logger.error(msg, **fields)``."""
    return MagicMock(error=error, warning=warning)


# ---------------------------------------------------------------------------
# The four family declarations (the card's (b))
# ---------------------------------------------------------------------------


class TestFamilyDeclarations:
    def test_marker_families_are_tenant_and_user(self) -> None:
        """Declared, not implicit: the two marker writers say so out loud."""
        assert _TENANT_INVALIDATION_POLICY.on_exhaustion is ExhaustionMarker.POISON_KEY
        assert _USER_INVALIDATION_POLICY.on_exhaustion is ExhaustionMarker.POISON_KEY

    def test_log_only_families_are_tier_and_rate_limit(self) -> None:
        assert _TIER_INVALIDATION_POLICY.on_exhaustion is ExhaustionMarker.LOG_ONLY
        assert _RATE_LIMIT_INVALIDATION_POLICY.on_exhaustion is ExhaustionMarker.LOG_ONLY

    def test_each_family_names_what_stays_stale(self) -> None:
        for policy in (
            _TENANT_INVALIDATION_POLICY,
            _TIER_INVALIDATION_POLICY,
            _RATE_LIMIT_INVALIDATION_POLICY,
            _USER_INVALIDATION_POLICY,
        ):
            assert policy.exhausted_message.endswith("may persist until TTL"), policy

    @pytest.mark.parametrize(
        "module_path",
        [
            "src/app/modules/tenants/service.py",
            "src/app/modules/tiers/service.py",
            "src/app/modules/rate_limits/service.py",
            "src/app/modules/users/cache.py",
        ],
    )
    def test_call_site_does_not_re_derive_the_retry_skeleton(self, module_path: str) -> None:
        """The duplication the card removes: a private ``for attempt in (1, 2)``.

        No behaviour test can catch a family that re-inherits an
        *identical* retry skeleton — it produces the same attempts and the
        same logs, so it only rots later. The skeleton is asserted to live in
        exactly one place, and each call site to reach for it.
        """
        from pathlib import Path

        source = (Path(__file__).resolve().parents[4] / module_path).read_text(encoding="utf-8")
        assert "for attempt in (1, 2)" not in source, f"{module_path} re-derives the retry skeleton"
        assert "invalidate_with_exhaustion(" in source, f"{module_path} must call the shared policy"


# ---------------------------------------------------------------------------
# Call sites: each routes through the ONE policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTenantCallSite:
    """tenants/service.py — marker family, marker TTL = TENANT_CACHE_TTL."""

    async def test_exhaustion_writes_tenant_poison_marker(self) -> None:
        from src.app.core.tenancy.cache import TENANT_CACHE_TTL
        from src.app.modules.tenants.service import TenantService

        service = TenantService(AsyncMock())
        tenant_id = _uuid()
        client = MagicMock()
        client.client = _client_with_pipeline(_failing())
        client.set = AsyncMock()

        with patch("src.app.modules.tenants.service.cache_client", client):
            await service._invalidate_tenant_cache(tenant_id, slug="acme")

        # One retry budget, not an unbounded loop. The L2 leg is now ONE
        # pipelined delete, so the attempts are counted on the pipeline's
        # execute, not on per-key client deletes.
        assert client.client.pipeline.call_count == len(INVALIDATION_ATTEMPTS)
        # The marker is the DECLARED tenant signal, bounded by the same TTL.
        client.set.assert_awaited_once()
        key, value, kwargs = client.set.call_args.args[0], client.set.call_args.args[1], client.set.call_args.kwargs
        assert key == f"tenant:invalidation_failed:{tenant_id}"
        assert value == "acme"
        assert kwargs["ttl"] == TENANT_CACHE_TTL == 300

    async def test_retry_recovers_without_marker(self) -> None:
        from src.app.modules.tenants.service import TenantService

        service = TenantService(AsyncMock())
        client = MagicMock()
        client.client = _client_with_pipeline(_fails_first_call())
        client.set = AsyncMock()

        with patch("src.app.modules.tenants.service.cache_client", client):
            await service._invalidate_tenant_cache(_uuid(), slug="acme")

        client.set.assert_not_awaited()


@pytest.mark.asyncio
class TestTierCallSite:
    """tiers/service.py — declared log-only: no marker, ever."""

    async def test_exhaustion_logs_but_writes_no_marker(self) -> None:
        from src.app.modules.tiers.service import TierService

        service = TierService(AsyncMock())
        client = MagicMock()
        client.delete = _failing()
        client.set = AsyncMock()

        with patch("src.app.modules.tiers.service.cache_client", client):
            await service._invalidate_tier_cache(["rate_limits:tier:7"], tier_name="7")

        assert client.delete.await_count == len(INVALIDATION_ATTEMPTS)
        client.set.assert_not_awaited()


@pytest.mark.asyncio
class TestRateLimitCallSite:
    """rate_limits/service.py — declared log-only: no marker, ever."""

    async def test_exhaustion_logs_but_writes_no_marker(self) -> None:
        from src.app.modules.rate_limits.service import RateLimitService

        service = RateLimitService(AsyncMock())
        client = MagicMock()
        client.delete = _failing()
        client.set = AsyncMock()

        with (
            patch("src.app.modules.rate_limits.service.cache_client", client),
            patch("src.app.core.security.rate_limit_cache.invalidate_tier_policy", new_callable=AsyncMock),
        ):
            await service._invalidate_tier_rate_limits_cache(7)

        assert client.delete.await_count == len(INVALIDATION_ATTEMPTS)
        client.set.assert_not_awaited()


@pytest.mark.asyncio
class TestUserCallSite:
    """users/cache.py — marker family, marker TTL = AUTH_INFO_CACHE_TTL."""

    async def test_exhaustion_writes_user_poison_marker(self) -> None:
        from src.app.modules.users.cache import AUTH_INFO_CACHE_TTL, UserCacheInvalidator
        from src.app.modules.users.session_invalidation import InvalidationReason

        client = _user_redis(delete_fails=True)
        user = {
            "id": "u-1",
            "email": "u@example.com",
            "username": "u1",
            "token_version": 3,
        }

        with patch("src.app.modules.users.cache.get_redis", return_value=client):
            await UserCacheInvalidator.invalidate_user_cache(
                user, bump_token_version=False, reason=InvalidationReason.PROFILE_EDIT
            )

        assert client.delete.await_count == len(INVALIDATION_ATTEMPTS)
        client.set.assert_awaited_once()
        args, kwargs = client.set.call_args
        assert args[0] == "user:invalidation_failed:u-1"
        assert args[1] == str(InvalidationReason.PROFILE_EDIT)
        assert kwargs["ex"] == AUTH_INFO_CACHE_TTL == 3600


def _user_redis(*, delete_fails: bool) -> MagicMock:
    """A Redis double whose pipeline is an async context manager."""
    client = MagicMock()
    client.delete = _failing() if delete_fails else AsyncMock()
    client.set = AsyncMock()

    class _Pipeline:
        def __init__(self) -> None:
            self.sets: list[str] = []

        async def __aenter__(self) -> _Pipeline:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.sets.append(key)

        async def execute(self) -> None:
            return None

    client.pipeline = MagicMock(return_value=_Pipeline())
    return client


def _uuid() -> Any:
    from uuid import uuid4

    return uuid4()
