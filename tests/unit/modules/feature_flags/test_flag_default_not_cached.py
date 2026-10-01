"""A caller's `default` must never be cached as if it were the flag's state.

``is_enabled`` and ``get_flag_value`` both cache under a key derived from
``(feature_name, tenant_id)`` — the ``default`` argument is not part of it.
When the flag exists nowhere (no registry row, no override, no tenant
JSONB), resolution fell through to the CALLER's ``default`` and that value
was written to the shared key. The next caller with a different ``default``
then read the first caller's fallback for the full ``CACHE_TTL``.

Latent rather than live only because the sole production caller passes
``False``; a second caller with a different default would have made it
immediate. Both accessors now cache only values sourced from the registry,
an override, or the tenant JSONB — those are decisions, independent of the
caller.

The boolean path had a SECOND leak of the same shape, which this file's
original cases could not see: ``cacheable`` was set from "the tenant has a
non-empty ``feature_flags`` map" rather than "this flag is in it", so an
ABSENT flag on any tenant that had configured some OTHER flag cached the
caller's default too. ``_jsonb_world`` below is that world.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.feature_flags import service as ff_service
from src.app.modules.feature_flags.service import FeatureFlagService

TENANT = uuid4()


@pytest.fixture
def store() -> dict:
    return {}


@pytest.fixture
def svc(store: dict):
    service = FeatureFlagService(AsyncMock())
    client = MagicMock()
    client.get = AsyncMock(side_effect=lambda k: store.get(k))
    client.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    client.delete = AsyncMock()
    with patch.object(ff_service, "cache_client", client):
        yield service


@contextlib.contextmanager
def _empty_world(service):
    """No registry row, no override, no tenant JSONB.

    The only thing left is the caller's ``default`` — which is the case
    under test.
    """
    with contextlib.ExitStack() as stack:
        for target, kwargs in (
            ("get_flag_by_key_or_none", {"return_value": None}),
            ("get_override", {"return_value": None}),
            ("get_tenant_flags", {"return_value": {}}),
        ):
            stack.enter_context(patch.object(service, target, new=AsyncMock(**kwargs)))
        stack.enter_context(patch.object(service.db, "get", new=AsyncMock(return_value=None)))
        yield


@contextlib.contextmanager
def _jsonb_world(service, flags: dict):
    """A tenant whose JSONB map is NON-EMPTY but does not carry the flag
    under test.

    This is the world the missing case lived in. ``is_enabled`` reached the
    JSONB tier and cached ``.get(feature_name, default)`` — the CALLER's
    default — under ``tenant:{tid}:feature:{name}``, so the first caller
    decided the answer for every other caller for CACHE_TTL. The existing
    ``test_a_tenant_jsonb_value_is_still_cached`` used a MATCHING flag
    name, so the suite stayed green while the invariant was violated.
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(service, "get_flag_by_key_or_none", new=AsyncMock(return_value=None)))
        stack.enter_context(patch.object(service, "get_override", new=AsyncMock(return_value=None)))
        stack.enter_context(patch.object(service, "get_tenant_flags", new=AsyncMock(return_value=dict(flags))))
        stack.enter_context(
            patch.object(service.db, "get", new=AsyncMock(return_value=MagicMock(feature_flags=dict(flags))))
        )
        yield


class TestIsEnabledDoesNotCacheTheCallersDefault:
    @pytest.mark.asyncio
    async def test_unknown_flag_default_is_not_cached(self, svc, store: dict) -> None:
        """Pre-fix: the first caller's default was written to the shared key."""
        with _empty_world(svc):
            assert await svc.is_enabled(TENANT, "ghost_flag", default=False) is False

        assert store == {}, f"a caller default was cached: {store}"

    @pytest.mark.asyncio
    async def test_a_later_caller_with_a_different_default_is_unaffected(self, svc, store: dict) -> None:
        """The end-to-end property: defaults cannot leak between callers."""
        with _empty_world(svc):
            first = await svc.is_enabled(TENANT, "ghost_flag", default=False)
            second = await svc.is_enabled(TENANT, "ghost_flag", default=True)

        assert first is False
        assert second is True, "the first caller's default was served to a caller that asked for True"

    @pytest.mark.asyncio
    async def test_a_registry_decision_is_still_cached(self, svc, store: dict) -> None:
        """The fix must not turn caching off entirely."""
        from src.app.modules.feature_flags.models import FeatureFlag

        flag = FeatureFlag(id=1, key="dark_mode", default_value=True, description="d")

        with (
            patch.object(svc, "get_flag_by_key_or_none", new=AsyncMock(return_value=flag)),
            patch.object(svc, "get_override", new=AsyncMock(return_value=None)),
        ):
            assert await svc.is_enabled(TENANT, "dark_mode", default=False) is True

        assert len(store) == 1, "a registry-sourced decision must still be cached"

    @pytest.mark.asyncio
    async def test_a_tenant_jsonb_value_is_still_cached(self, svc, store: dict) -> None:
        with (
            patch.object(svc, "get_flag_by_key_or_none", new=AsyncMock(return_value=None)),
            patch.object(svc.db, "get", new=AsyncMock(return_value=MagicMock(feature_flags={"beta": True}))),
        ):
            assert await svc.is_enabled(TENANT, "beta", default=False) is True

        assert len(store) == 1, "a tenant JSONB decision must still be cached"

    # -- the non-empty JSONB map that does NOT carry the flag -------------

    @pytest.mark.asyncio
    async def test_absent_flag_in_a_non_empty_jsonb_map_is_not_cached(self, svc, store: dict) -> None:
        """The case the suite was missing: map present, flag absent.

        ``cacheable`` used to key on "the tenant has a non-empty map"
        rather than "this flag is in it", so the caller's default was
        written to the shared key.
        """
        with _jsonb_world(svc, {"beta": True}):
            assert await svc.is_enabled(TENANT, "ghost_flag", default=False) is False

        assert store == {}, f"a caller default was cached for an absent flag: {store}"

    @pytest.mark.asyncio
    async def test_absent_flag_does_not_leak_a_previous_callers_default(self, svc, store: dict) -> None:
        """The end-to-end property, in the world that actually broke it."""
        with _jsonb_world(svc, {"beta": True}):
            first = await svc.is_enabled(TENANT, "ghost_flag", default=False)
            second = await svc.is_enabled(TENANT, "ghost_flag", default=True)

        assert first is False
        assert second is True, "the first caller's default was served to a caller that asked for True"

    @pytest.mark.asyncio
    async def test_arbitrary_flag_names_do_not_mint_one_key_per_request(self, svc, store: dict) -> None:
        """``GET /features/{flag_name}`` takes an unconstrained path param.

        With the JSONB tier over-eager, every authenticated request minted
        a fresh ``tenant:{tid}:feature:<random>`` key that lived for
        CACHE_TTL — an authenticated key-spray bounded only by TTL.
        """
        with _jsonb_world(svc, {"beta": True}):
            for i in range(5):
                await svc.is_enabled(TENANT, f"sprayed_{i}", default=False)

        assert store == {}, f"absent flag names minted cache keys: {store}"

    @pytest.mark.asyncio
    async def test_a_jsonb_flag_present_is_still_the_decision(self, svc, store: dict) -> None:
        """The guard must not disable caching for the flag that IS there."""
        with _jsonb_world(svc, {"beta": False}):
            assert await svc.is_enabled(TENANT, "beta", default=True) is False

        assert len(store) == 1, "a present JSONB flag must still be cached"


class TestGetFlagValueDoesNotCacheTheCallersDefault:
    @pytest.mark.asyncio
    async def test_unknown_flag_default_is_not_cached(self, svc, store: dict) -> None:
        with _empty_world(svc):
            await svc.get_flag_value(TENANT, "ghost_value", default="fallback")

        assert store == {}, f"a caller default was cached: {store}"

    @pytest.mark.asyncio
    async def test_a_later_caller_with_a_different_default_is_unaffected(self, svc, store: dict) -> None:
        with _empty_world(svc):
            first = await svc.get_flag_value(TENANT, "ghost_value", default="a")
            second = await svc.get_flag_value(TENANT, "ghost_value", default="b")

        assert first == "a"
        assert second == "b", "the first caller's default leaked to the second caller"

    @pytest.mark.asyncio
    async def test_a_registry_value_is_still_cached(self, svc, store: dict) -> None:
        from src.app.modules.feature_flags.models import FeatureFlag

        flag = FeatureFlag(id=1, key="limits", default_value="reg", description="d")

        with (
            patch.object(svc, "get_flag_by_key_or_none", new=AsyncMock(return_value=flag)),
            patch.object(svc, "get_override", new=AsyncMock(return_value=None)),
        ):
            assert await svc.get_flag_value(TENANT, "limits", default="caller") == "reg"

        assert len(store) == 1, "a registry-sourced value must still be cached"

    @pytest.mark.asyncio
    async def test_absent_flag_in_a_non_empty_jsonb_map_is_not_cached(self, svc, store: dict) -> None:
        """Typed sibling of the boolean case, for the same tenant shape."""
        with _jsonb_world(svc, {"beta": True}):
            assert await svc.get_flag_value(TENANT, "ghost_value", default="fallback") == "fallback"

        assert store == {}, f"a caller default was cached for an absent flag: {store}"


class TestResolveContract:
    @pytest.mark.asyncio
    async def test_resolve_reports_cacheability(self, svc) -> None:
        """The (value, cacheable) contract the cache write depends on."""
        with _empty_world(svc):
            assert await svc._resolve_flag_value(TENANT, "nope", default=7) == (7, False)

        from src.app.modules.feature_flags.models import FeatureFlag

        flag = FeatureFlag(id=1, key="k", default_value=1, description="d")
        with (
            patch.object(svc, "get_flag_by_key_or_none", new=AsyncMock(return_value=flag)),
            patch.object(svc, "get_override", new=AsyncMock(return_value=None)),
        ):
            assert await svc._resolve_flag_value(TENANT, "k", default=7) == (1, True)

    def test_cache_key_does_not_contain_the_default(self) -> None:
        """Documents the constraint the cacheable guard exists to satisfy."""
        from src.app.core.utils.cache import build_tenant_key

        a = build_tenant_key("feature:ghost", TENANT)
        b = build_tenant_key("feature:ghost", TENANT)
        assert a == b, "the key is (name, tenant) only — which is why default is never cached"
