"""Regression tests for verified report findings (feature flags).

Covers: F1 (registry purge across tenants), F27 (get_flag_value caching
symmetric with is_enabled), F28 (warmed-row completeness).
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import UUID

import pytest

from src.app.core.cache.client import RedisCacheAdapter, cache_client
from src.app.modules.feature_flags.models import FeatureFlag
from src.app.modules.feature_flags.service import FeatureFlagService

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


class FakeRaw:
    """Raw-client stand-in supporting SCAN + DELETE for the registry purge."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def scan(self, cursor: int, match: str, count: int = 500) -> tuple[int, list[str]]:
        import fnmatch

        return 0, [k for k in self.data if fnmatch.fnmatch(k, match)]

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed


def _svc() -> FeatureFlagService:
    return FeatureFlagService(db=MagicMock())


@pytest.mark.asyncio
async def test_f1_registry_purge_clears_all_tenants() -> None:
    raw = FakeRaw()
    raw.data = {
        f"tenant:{TENANT_A}:feature:dark_mode": "true",
        f"tenant:{TENANT_B}:feature:dark_mode": "false",
        f"tenant:{TENANT_A}:feature_value:dark_mode": "true",
        f"tenant:{TENANT_A}:features": "{}",
        f"tenant:{TENANT_A}:feature:other": "true",
        "feature_flag:dark_mode": "{}",
    }
    svc = _svc()
    with patch.object(RedisCacheAdapter, "client", new_callable=PropertyMock, return_value=raw):
        await svc._purge_registry_caches("dark_mode")
    assert f"tenant:{TENANT_A}:feature:dark_mode" not in raw.data
    assert f"tenant:{TENANT_B}:feature:dark_mode" not in raw.data
    assert f"tenant:{TENANT_A}:feature_value:dark_mode" not in raw.data
    assert f"tenant:{TENANT_A}:features" not in raw.data
    # Unrelated flag untouched.
    assert f"tenant:{TENANT_A}:feature:other" in raw.data


@pytest.mark.asyncio
async def test_f1_invalidate_cache_covers_value_key() -> None:
    svc = _svc()
    deleter = AsyncMock(return_value=1)
    with patch.object(cache_client, "delete", deleter):
        await svc._invalidate_cache(TENANT_A, "dark_mode")
    called = deleter.call_args.args
    joined = " ".join(called)
    assert "features" in joined
    assert "feature:dark_mode" in joined
    assert "feature_value:dark_mode" in joined


@pytest.mark.asyncio
async def test_f27_flag_value_cached_symmetrically() -> None:
    store: dict[str, str] = {}
    svc = _svc()
    # _resolve_flag_value now returns (value, cacheable); this case is a
    # real registry/JSONB-sourced decision, so it is cacheable.
    resolver = AsyncMock(return_value=({"max_users": 10}, True))
    with (
        patch.object(svc, "_resolve_flag_value", resolver),
        patch.object(cache_client, "get", AsyncMock(side_effect=lambda k: store.get(k))),
        patch.object(
            cache_client,
            "set",
            AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v)),
        ),
    ):
        first = await svc.get_flag_value(TENANT_A, "limits", default=None)
        second = await svc.get_flag_value(TENANT_A, "limits", default=None)
    assert first == {"max_users": 10}
    assert second == {"max_users": 10}
    resolver.assert_awaited_once()  # second call served from cache
    assert len(store) == 1
    (cache_key, payload), = store.items()
    assert "feature_value:limits" in cache_key
    assert "11111111" in cache_key


def test_f28_warmed_dict_reconstructs_registry_row() -> None:
    """The warmer's dict must carry every constructor-accepted column."""
    data = {
        "id": 1,
        "key": "dark_mode",
        "description": "d",
        "default_value": True,
        "value_type": "boolean",
        "flag_metadata": {"tier": "pro"},
        "is_system": False,
    }
    flag = FeatureFlag(**data)
    assert flag.key == "dark_mode"
    assert flag.flag_metadata == {"tier": "pro"}
    assert flag.default_value is True
    # json round-trip, as the reader does:
    import json as _json

    clone = FeatureFlag(**_json.loads(_json.dumps(data)))
    assert clone.flag_metadata == {"tier": "pro"}


def test_f37_lock_table_bounded() -> None:
    from src.app.modules.feature_flags import service as svc_mod

    assert svc_mod._FLAG_LOCK_CAP == 4096
