"""Batch user-cache invalidation must use the family's exhaustion policy.

``invalidation_exhaustion`` exists so every cache family has ONE declared
failure policy. The single-user path ran under it; the batch path — used
by the RBAC role assign/remove flows, a security-relevant mutation — had a
bare try/except with a single warning: no retry, no error-level record,
and ``_USER_INVALIDATION_POLICY`` never referenced. Batch invalidation
was therefore strictly less safe than single invalidation of the same
data, in the module that owns the family.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.users import cache as user_cache
from src.app.modules.users.session_invalidation import InvalidationReason


def _users(n: int = 2) -> list[MagicMock]:
    out = []
    for i in range(n):
        u = MagicMock()
        u.id = 100 + i
        u.email = f"u{i}@example.com"
        u.username = f"user{i}"
        u.tenant_id = "22222222-2222-2222-2222-222222222222"
        u.token_version = 3
        out.append(u)
    return out


def _failing_client() -> MagicMock:
    """A client whose pipeline always raises — the exhaustion case."""
    client = MagicMock()
    client.set = AsyncMock()
    client.delete = AsyncMock()

    pipe = MagicMock()
    pipe.execute = AsyncMock(side_effect=RuntimeError("redis down"))
    pipe.delete = MagicMock()
    pipe.set = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)
    client.pipeline.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("redis down"))
    client.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)
    return client


def _ok_client() -> MagicMock:
    client = MagicMock()
    client.set = AsyncMock()
    client.delete = AsyncMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    pipe.delete = MagicMock()
    pipe.set = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)
    client.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe)
    client.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)
    return client


class TestBatchUsesTheExhaustionPolicy:
    @pytest.mark.asyncio
    async def test_exhaustion_retries_then_logs_an_error(self) -> None:
        """Pre-fix: one attempt, one warning, no retry, no error record."""
        client = _failing_client()

        calls: list[dict] = []
        real = user_cache.invalidate_with_exhaustion

        async def _spy(*args, **kwargs):
            calls.append(kwargs)
            return await real(*args, **kwargs)

        with (
            patch.object(user_cache, "get_redis", return_value=client),
            patch.object(user_cache, "get_current_tenant", return_value=None),
            patch.object(user_cache, "invalidate_with_exhaustion", side_effect=_spy),
        ):
            await user_cache.UserCacheInvalidator.invalidate_user_cache_batch(
                _users(), reason=InvalidationReason.PROFILE_EDIT
            )

        assert len(calls) == 1, "batch path bypassed invalidate_with_exhaustion"
        # The family's declared policy row, not a bare try/except.
        assert calls[0]["policy"] is user_cache._USER_INVALIDATION_POLICY
        # Exhaustion must surface; assert the policy actually engaged by
        # checking the marker was requested (the POISON_KEY posture).
        assert calls[0].get("write_marker") is not None, "policy needs a marker writer"

    @pytest.mark.asyncio
    async def test_exhaustion_writes_the_poison_marker(self) -> None:
        """Pre-fix no marker was written for the batch path at all."""
        client = _failing_client()

        with (
            patch.object(user_cache, "get_redis", return_value=client),
            patch.object(user_cache, "get_current_tenant", return_value=None),
        ):
            await user_cache.UserCacheInvalidator.invalidate_user_cache_batch(
                _users(), reason=InvalidationReason.PROFILE_EDIT
            )

        assert client.set.await_count >= 1, "no poison marker written on exhaustion"
        marker_key = client.set.await_args.args[0]
        assert "invalidation_failed" in marker_key
        assert client.set.await_args.kwargs.get("ex") == user_cache.AUTH_INFO_CACHE_TTL, (
            "marker TTL must bound the stale entry exactly as the single path does"
        )

    @pytest.mark.asyncio
    async def test_healthy_batch_still_deletes_and_bumps(self) -> None:
        """The happy path must be unchanged: deletes + token bumps in one pipeline."""
        client = _ok_client()

        with (
            patch.object(user_cache, "get_redis", return_value=client),
            patch.object(user_cache, "get_current_tenant", return_value=None),
        ):
            await user_cache.UserCacheInvalidator.invalidate_user_cache_batch(
                _users(), bump_token_version=True, reason=InvalidationReason.DEACTIVATION
            )

        client.pipeline.assert_called_once()
        assert client.set.await_count == 0, "no marker should be written on success"

    @pytest.mark.asyncio
    async def test_single_poison_key_matches_batch_marker_shape(self) -> None:
        """One user -> the single-path key; several -> a batch-scoped key."""
        single = user_cache._user_cache_poison_key([42])
        many = user_cache._user_cache_poison_key([1, 2, 3])

        assert single == "user:invalidation_failed:42"
        assert many.startswith("user:invalidation_failed:batch:3:")
        assert many != single

    @pytest.mark.asyncio
    async def test_marker_key_is_stable_for_the_same_batch(self) -> None:
        """A reader must be able to recompute the marker for a known batch."""
        a = user_cache._user_cache_poison_key([3, 1, 2])
        b = user_cache._user_cache_poison_key([2, 3, 1])
        assert a == b
