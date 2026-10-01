"""Feature flags service with BaseService pattern.

The decision module for feature flags. It owns the whole tenant-JSONB read,
the registry/override precedence, typed and boolean parsing, the per-key
singleflight, and cache invalidation. A caller supplies a tenant and a flag
name and gets a decision; it never builds a cache key, chooses a precedence
step, or decodes a raw value.

Precedence, highest wins: tenant override
(``FeatureFlagOverride.custom_value``) -> registry default
(``FeatureFlag.default_value``) -> legacy tenant JSONB
(``Tenant.feature_flags``) -> the caller's ``default``.

``parse_flag_bool`` moved here from the former ``feature_flags.utils``, which
also held ``get_feature_flags`` — a second implementation of the tenant-JSONB
read that had no production caller and wrote the *same* Redis key
(``build_tenant_key("features", tenant_id)``) as ``get_tenant_flags`` below.
Two implementations, one cache entry, two ``CACHE_TTL`` constants. Deleted.
"""

import asyncio
from collections.abc import Awaitable, Sequence
from typing import Any, TypeVar, cast

T = TypeVar("T")
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.app.core.cache.client import (
    cache_client,  # P2: ONE Redis access surface (adapter resolves the client at call time)
)
from src.app.core.cache.warmer import warmer
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis  # warmer pipeline only (see warm_feature_flags)
from src.app.core.utils.cache import build_tenant_key
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules._base import BaseService, ModelNotFoundError
from src.app.modules.feature_flags.crud import crud_feature_flag_overrides, crud_feature_flags
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.schemas import (
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideUpdate,
    FeatureFlagRead,
    FeatureFlagUpdate,
)
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

CACHE_TTL = 300  # 5 minutes


def parse_flag_bool(value: Any) -> bool:
    """Accept only boolean values, integer 1, and case-insensitive true/1 text.

    False/0 and unsupported typed values evaluate to False, never truthiness.
    Redis bytes use the same contract without decoding arbitrary content.
    """
    if isinstance(value, str | bytes):
        return value.lower() in ("true", "1", b"true", b"1")
    return value is True or (type(value) is int and value == 1)


def _override_effective_value(override: FeatureFlagOverride | None) -> tuple[bool, Any]:
    """Resolve a tenant override into ``(supplied, value)``.

    Shared by the typed path (``get_flag_value``) and the boolean path
    (``is_enabled``), which differed only in what they did with the leaf. The
    ``custom_value`` dict shape — ``value`` preferred over ``enabled`` — is
    walked here once, so the two paths cannot disagree about which key wins.

    ``(False, None)`` means no override supplied a value and the registry
    default applies.
    """
    if override is None:
        return False, None
    custom_value = get_user_attr(override, "custom_value")
    if custom_value is None:
        return False, None
    if isinstance(custom_value, dict):
        if "value" in custom_value:
            return True, custom_value["value"]
        if "enabled" in custom_value:
            return True, custom_value["enabled"]
    return True, custom_value


# P2.19 FIX: per-key singleflight locks for the manual feature-flag caches.
# On a cold key under burst traffic, N concurrent requests would each miss
# the cache and hit the DB (classic cache stampede). These locks serialize
# the cache-miss path so only one coroutine per key loads from the DB; the
# others wait and then read the freshly-written cache value.
_flag_cache_locks: dict[str, asyncio.Lock] = {}

# F37: bound the singleflight lock table (cardinality = tenants x flags).
# Evicting a live lock only loses batching for one miss, never correctness
# (the next contender creates a fresh lock), so FIFO eviction is safe.
_FLAG_LOCK_CAP = 4096


async def _with_flag_lock(key: str, coro: Awaitable[T]) -> T:
    """Run `coro` under a per-key lock (singleflight)."""
    import asyncio

    lock = _flag_cache_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        if len(_flag_cache_locks) >= _FLAG_LOCK_CAP:
            for old in list(_flag_cache_locks)[: _FLAG_LOCK_CAP // 10]:
                _flag_cache_locks.pop(old, None)
        _flag_cache_locks[key] = lock
    async with lock:
        return await coro


class FeatureFlagService(BaseService[FeatureFlag]):
    """Service for managing feature flags using BaseService pattern."""

    def __init__(self, db: AsyncSession):
        super().__init__(model=FeatureFlag, db=db, select_schema=FeatureFlagRead)
        self.crud = crud_feature_flags
        self.override_crud = crud_feature_flag_overrides

    # =========================================================================
    # Core Flag Operations
    # =========================================================================

    async def get_flag_by_key(self, key: str) -> FeatureFlag:
        """
        Get a feature flag by its unique key.

        Args:
            key: Unique feature flag key

        Returns:
            FeatureFlag entity

        Raises:
            ModelNotFoundError: If flag doesn't exist
        """
        result = await self.crud.get(db=self.db, key=key)
        if not result:
            raise ModelNotFoundError("FeatureFlag", key)
        flag: FeatureFlag = result  # type: ignore[assignment]
        return flag

    async def get_flag_by_key_or_none(self, key: str) -> FeatureFlag | None:
        """Get a feature flag by key, returns None if not found.
        First checks the global feature flag cache warmed by the application.
        """
        # P2: routed through cache_client — the adapter resolves the shared
        # client at call time and no-ops when Redis is down, so the explicit
        # None-guard is gone. Q-M3 warning semantics preserved below.
        try:
            cache_key = f"feature_flag:{key}"
            cached = await cache_client.get(cache_key)
            if cached:
                data = json_loads(cached)
                # Return an actual FeatureFlag model to satisfy type checker,
                # but it's detached from session
                return FeatureFlag(**data)
        except Exception as e:
            # Q-M3: cache read failure is production-relevant (serves stale
            # defaults while degraded) — warn, don't hide at debug.
            logger.warning("Feature flag cache get failed", key=key, error=str(e))

        result = await self.crud.get(db=self.db, key=key)
        if result is None:
            return None
        flag: FeatureFlag = result  # type: ignore[assignment]
        return flag

    async def create_flag(self, data: FeatureFlagCreate) -> FeatureFlag:
        """
        Create a new feature flag.

        Args:
            data: Feature flag creation data

        Returns:
            Created FeatureFlag entity
        """
        # C-4/B8: FastCRUD.create returns None unless schema_to_select is
        # passed (verified against fastcrud 0.20.1) — re-read the row to
        # honor the FeatureFlag return contract.
        await self.create(**data.model_dump())
        result = await self.crud.get(db=self.db, key=data.key)
        if result is None:
            raise ModelNotFoundError("FeatureFlag", data.key)
        # C-4: dict-shaped results normalized to the ORM model contract;
        # already-typed results (real models, test doubles) pass through.
        if isinstance(result, dict):
            return FeatureFlag(**result)
        return result  # type: ignore[return-value]

    async def update_flag(self, flag_id: int, data: FeatureFlagUpdate) -> FeatureFlag:
        """
        Update an existing feature flag.

        Args:
            flag_id: ID of the flag to update
            data: Update data

        Returns:
            Updated FeatureFlag entity

        Raises:
            ModelNotFoundError: If flag doesn't exist
        """
        # C-4/B8: FastCRUD.update returns None unless return_columns is
        # passed (verified against fastcrud 0.20.1 execution.py) — the
        # old `if not flag: raise` therefore 404'd every real update.
        # Re-read the row after the write to source both the not-found
        # decision and the returned entity.
        # crud.update directly: this method re-reads below for the ORM entity
        # (its return contract), so the typed BaseService path would re-read twice.
        await self.crud.update(db=self.db, object=data.model_dump(exclude_unset=True), id=flag_id)
        flag = await self.crud.get(db=self.db, id=flag_id)
        if flag is None:
            raise ModelNotFoundError("FeatureFlag", flag_id)

        flag_key = flag.key if not isinstance(flag, dict) else flag.get("key")
        if flag_key:
            try:
                await cache_client.delete(f"feature_flag:{flag_key}")
                # F1: registry mutations must purge per-tenant decisions
                # fleet-wide — tenant:*:feature:{key} / :features /
                # :feature_value:{key} survive 300s otherwise.
                await self._purge_registry_caches(flag_key)
            except Exception as e:
                # Q-M3: failed invalidation leaves stale flag values until TTL
                # — warn with the flag key so operators can flush manually.
                logger.warning("Feature flag cache invalidation failed", flag_key=flag_key, error=str(e))

        # C-4: dict-shaped results normalized to the ORM model contract;
        # already-typed results (real models, test doubles) pass through.
        if isinstance(flag, dict):
            return FeatureFlag(**flag)
        return flag  # type: ignore[return-value]

    async def delete_flag(self, flag_id: int) -> None:
        """
        Delete a feature flag.

        Args:
            flag_id: ID of the flag to delete
        """
        result = await self.crud.get(db=self.db, id=flag_id)
        if result:
            flag_key = result.key if not isinstance(result, dict) else result.get("key")
            if flag_key:
                try:
                    await cache_client.delete(f"feature_flag:{flag_key}")
                    # F1: see update_flag — a deleted registry row must also
                    # stop serving per-tenant decisions.
                    await self._purge_registry_caches(flag_key)
                except Exception as e:
                    # Q-M3: failed invalidation leaves the deleted flag served
                    # from cache until TTL — warn with the flag key.
                    logger.warning("Feature flag cache invalidation failed", flag_key=flag_key, error=str(e))
        await self.delete(flag_id)

    # =========================================================================
    # Override Operations
    # =========================================================================

    async def get_override(self, feature_flag_id: int, tenant_id: UUID) -> FeatureFlagOverride | None:
        """Get a feature flag override for a tenant."""
        result = await self.override_crud.get(db=self.db, feature_flag_id=feature_flag_id, tenant_id=tenant_id)
        if result is None:
            return None
        override: FeatureFlagOverride = result  # type: ignore[assignment]
        return override

    async def create_override(self, data: FeatureFlagOverrideCreate) -> FeatureFlagOverride:
        """Create a feature flag override."""
        result = await self.override_crud.create(db=self.db, object=data.model_dump())
        override: FeatureFlagOverride = result  # type: ignore[assignment]

        # Invalidate cache
        ff_id = override.get("feature_flag_id") if isinstance(override, dict) else override.feature_flag_id
        t_id = override.get("tenant_id") if isinstance(override, dict) else override.tenant_id

        if ff_id and t_id:
            flag = await self.db.get(FeatureFlag, ff_id)
            if flag and flag.key:
                await self._invalidate_cache(t_id, flag.key)

        return override

    async def update_override(self, override_id: int, data: FeatureFlagOverrideUpdate) -> FeatureFlagOverride | None:
        """Update a feature flag override.

        Tenant isolation is enforced by TenantAwareCRUD.update, which auto-injects
        the current tenant_id into the WHERE clause so a caller can only mutate
        their own tenant's override (IDOR-safe).
        """
        # Re-read to source the entity, exactly as update_flag does above.
        # Without it the write lands but the cache invalidation below is
        # unreachable. update() returns the row only when asked for
        # return_columns (P1-4 changed it to forward FastCRUD's return rather
        # than discard it), so a re-read is the portable way to source the
        # entity regardless of schema.
        await self.override_crud.update(db=self.db, id=override_id, object=data.model_dump(exclude_unset=True))
        result = await self.override_crud.get(db=self.db, id=override_id)
        if result is None:
            return None
        override: FeatureFlagOverride = result  # type: ignore[assignment]

        # Invalidate cache
        ff_id = override.get("feature_flag_id") if isinstance(override, dict) else override.feature_flag_id
        t_id = override.get("tenant_id") if isinstance(override, dict) else override.tenant_id

        if ff_id and t_id:
            flag = await self.db.get(FeatureFlag, ff_id)
            if flag and flag.key:
                await self._invalidate_cache(t_id, flag.key)

        return override

    async def delete_override(self, override_id: int) -> None:
        """Delete a feature flag override."""
        # Need to fetch the override to get its tenant_id and feature_flag_id before deletion
        override = await self.override_crud.get(db=self.db, id=override_id)
        if override:
            ff_id = override.get("feature_flag_id") if isinstance(override, dict) else override.feature_flag_id
            t_id = override.get("tenant_id") if isinstance(override, dict) else override.tenant_id

            if ff_id and t_id:
                flag = await self.db.get(FeatureFlag, ff_id)
                if flag and flag.key:
                    await self._invalidate_cache(t_id, flag.key)

        await self.override_crud.delete(db=self.db, id=override_id)

    # =========================================================================
    # Tenant Feature Flags (JSONB-based)
    # =========================================================================

    async def get_tenant_flags(self, tenant_id: UUID) -> dict[str, Any]:
        """
        Get all feature flags for a tenant from JSONB.

        Args:
            tenant_id: Tenant UUID

        Returns:
            Dictionary of feature flags
        """
        cache_key = build_tenant_key("features", tenant_id)

        # Fast path: serve from cache if present (via cache_client adapter).
        try:
            cached_value = await cache_client.get(cache_key)
            if cached_value:
                return cast("dict[str, Any]", json_loads(cached_value))
        except Exception as e:
            # Q-M3: cache read failure serves DB-default flags while degraded.
            logger.warning("Feature flag tenant cache get failed", cache_key=cache_key, error=str(e))

        async def _load() -> dict[str, Any]:
            # P2.19 FIX: re-check cache now that we hold the per-key lock,
            # so a concurrent caller that already populated it is served from cache
            # instead of stampeding the DB.
            try:
                cached_value = await cache_client.get(cache_key)
                if cached_value:
                    return cast("dict[str, Any]", json_loads(cached_value))
            except Exception as e:
                # Q-M3: cache read failure serves DB flags while degraded.
                logger.warning("Tenant feature flags cache get failed", cache_key=cache_key, error=str(e))

            tenant = await self.db.get(Tenant, tenant_id)
            flags: dict[str, Any] = {}
            if tenant and tenant.feature_flags:
                flags = tenant.feature_flags

            try:
                await cache_client.set(cache_key, json_dumps(flags, as_str=True), ttl=CACHE_TTL)
            except Exception as e:
                # Q-M3: cache write failure means every read pays the DB cost
                # until recovery — warn, don't hide at debug.
                logger.warning("Tenant feature flags cache write failed", cache_key=cache_key, error=str(e))
            return flags

        return await _with_flag_lock(cache_key, _load())

    async def get_flag_value(self, tenant_id: UUID, feature_name: str, default: Any = None) -> Any:
        """Get the typed value of a feature flag for a tenant.

        Unlike :meth:`is_enabled` (boolean-only), this honors ``value_type``:
        string/number/json values from a tenant override or the legacy
        tenant JSONB store are returned as-is. Resolution order mirrors
        ``is_enabled``: tenant override → registry default → tenant JSONB →
        ``default``. The registry ``default_value`` column is boolean, so
        non-boolean defaults only exist in overrides / tenant JSONB.

        Freshness contract (F27, shared with :meth:`is_enabled`): decisions
        are cached per tenant for ``CACHE_TTL`` (300s). Registry mutations
        (``update_flag``/``delete_flag``) and override mutations purge via
        ``_invalidate_cache``/``_purge_registry_caches``, so the two
        accessors can disagree only within one TTL window after an
        unpurged write — never indefinitely.
        """
        cache_key = build_tenant_key(f"feature_value:{feature_name}", tenant_id)

        try:
            cached = await cache_client.get(cache_key)
            if cached is not None:
                return json_loads(cached)
        except Exception as e:
            logger.warning("Feature flag value cache get failed", cache_key=cache_key, error=str(e))

        async def _load() -> Any:
            try:
                cached = await cache_client.get(cache_key)
                if cached is not None:
                    return json_loads(cached)
            except Exception as e:
                logger.warning("Feature flag value cache get failed", cache_key=cache_key, error=str(e))

            value, cacheable = await self._resolve_flag_value(tenant_id, feature_name, default)

            if cacheable:
                try:
                    await cache_client.set(cache_key, json_dumps(value, as_str=True), ttl=CACHE_TTL)
                except Exception as e:
                    logger.warning("Feature flag value cache write failed", cache_key=cache_key, error=str(e))
            return value

        return await _with_flag_lock(cache_key, _load())

    async def _resolve_flag_value(self, tenant_id: UUID, feature_name: str, default: Any = None) -> tuple[Any, bool]:
        """Uncached B7 precedence resolution shared by ``get_flag_value``.

        Returns ``(value, cacheable)``. ``cacheable`` is False when the value
        fell through to the CALLER's ``default`` — that is one caller's
        fallback, not the flag's state, and the cache key
        (``feature_value:{name}``) does not include ``default``. Caching it
        would serve the first caller's default to everyone else for
        CACHE_TTL.
        """
        flag = await self.get_flag_by_key_or_none(feature_name)
        if flag is not None:
            flag_id = int(get_user_attr(flag, "id", 0))
            supplied, value = _override_effective_value(await self.get_override(flag_id, tenant_id))
            if supplied:
                return value, True
            return get_user_attr(flag, "default_value", False), True
        tenant_flags = await self.get_tenant_flags(tenant_id)
        if feature_name in tenant_flags:
            return tenant_flags[feature_name], True
        return default, False

    async def is_enabled(self, tenant_id: UUID, feature_name: str, default: bool = False) -> bool:
        """Check if a feature is enabled for a tenant.

        B7 precedence contract (highest wins): tenant override
        (FeatureFlagOverride.custom_value) → registry default
        (FeatureFlag.default_value) → legacy tenant JSONB
        (Tenant.feature_flags) → ``default``.

        Boolean-only: string/number/json flags are NOT coerced here (a
        ``"false"`` string is not ``False``) — non-boolean flags evaluated
        through this method read as ``False`` unless literally ``"true"``.
        Use :meth:`get_flag_value` or :meth:`get_tenant_flags` for typed
        values.

        Freshness: cached per tenant for ``CACHE_TTL`` (300s); see
        :meth:`get_flag_value` for the shared contract.
        """
        cache_key = build_tenant_key(f"feature:{feature_name}", tenant_id)

        # Fast path: serve from cache if present (via cache_client adapter).
        try:
            cached_value = await cache_client.get(cache_key)
            if cached_value is not None:
                # BUG FIX: .decode() crashes when Redis has decode_responses=True
                # (value is already a str). Use a safe conversion for both cases.
                return parse_flag_bool(cached_value)
        except Exception as e:
            # Q-M3: cache read failure serves DB-default flags while degraded.
            logger.warning("Feature flag tenant cache get failed", cache_key=cache_key, error=str(e))

        async def _load() -> bool:
            # P2.19 FIX: re-check cache under the per-key lock (singleflight)
            # so concurrent cache misses don't all hit the DB.
            try:
                cached_value = await cache_client.get(cache_key)
                if cached_value is not None:
                    return parse_flag_bool(cached_value)
            except Exception as e:
                # Q-M3: cache read failure serves DB-default flags while degraded.
                logger.warning("Feature flag enabled-state cache get failed", cache_key=cache_key, error=str(e))

            # Fallback to DB (H-12 FIX: Check flag and overrides first)
            enabled = default

            flag = await self.get_flag_by_key_or_none(feature_name)
            # Only a value sourced from the registry, an override, or the
            # tenant JSONB is a DECISION and safe to cache. A value that
            # fell through to the caller's `default` is that one caller's
            # fallback, not the flag's state — caching it under a key that
            # omits `default` would serve the first caller's default to
            # every other caller for CACHE_TTL.
            cacheable = False
            if flag:
                cacheable = True
                flag_id = int(get_user_attr(flag, "id", 0))
                flag_default = parse_flag_bool(get_user_attr(flag, "default_value", False))

                override = await self.get_override(flag_id, tenant_id)
                supplied, value = _override_effective_value(override)
                if supplied:
                    enabled = parse_flag_bool(value)
                else:
                    enabled = flag_default
            else:
                tenant = await self.db.get(Tenant, tenant_id)
                # The JSONB tier is consulted ONLY for a flag the map
                # actually contains. A non-empty map that lacks THIS flag
                # says nothing about it, so `.get(feature_name, default)`
                # would return the CALLER's default and marking it
                # cacheable would write that fallback to
                # tenant:{tid}:feature:{name} — a key that does not
                # include `default` — serving the first caller's default
                # to every other caller for CACHE_TTL. It would also mint
                # one fresh 300s key per arbitrary flag name requested,
                # because `GET /features/{flag_name}` takes the name as an
                # unconstrained path param.
                # Mirrors `_resolve_flag_value`: absent -> not cacheable.
                if tenant and tenant.feature_flags and feature_name in tenant.feature_flags:
                    enabled = parse_flag_bool(tenant.feature_flags[feature_name])
                    cacheable = True

            if cacheable:
                try:
                    await cache_client.set(cache_key, str(enabled).lower(), ttl=CACHE_TTL)
                except Exception as e:
                    # Q-M3: cache write failure means every check pays the full
                    # DB+override resolution until recovery — warn.
                    logger.warning("Feature flag enabled-state cache write failed", cache_key=cache_key, error=str(e))
            return enabled

        return await _with_flag_lock(cache_key, _load())

    async def update_tenant_flag(self, tenant_id: UUID, feature_name: str, value: Any) -> Tenant:
        """
        Update a single feature flag for a tenant.

        Args:
            tenant_id: Tenant UUID
            feature_name: Name of the feature flag
            value: Value to set

        Returns:
            Updated Tenant entity

        Raises:
            ModelNotFoundError: If tenant doesn't exist
        """
        tenant = await self._get_tenant_for_update(tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None:
            tenant.feature_flags = {}
        tenant.feature_flags[feature_name] = value
        flag_modified(tenant, "feature_flags")

        return await self._commit_and_invalidate(tenant, tenant_id, [feature_name])

    async def delete_tenant_flag(self, tenant_id: UUID, flag_name: str) -> Tenant:
        """
        Remove a feature flag from tenant configuration.

        Args:
            tenant_id: Tenant UUID
            flag_name: Name of the flag to remove

        Returns:
            Updated Tenant entity

        Raises:
            ModelNotFoundError: If tenant or flag doesn't exist
        """
        tenant = await self._get_tenant_for_update(tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None or flag_name not in tenant.feature_flags:
            raise ModelNotFoundError("FeatureFlag", flag_name)
        del tenant.feature_flags[flag_name]
        flag_modified(tenant, "feature_flags")

        return await self._commit_and_invalidate(tenant, tenant_id, [flag_name])

    async def bulk_update_tenant_flags(self, tenant_id: UUID, flags: dict[str, Any]) -> Tenant:
        """
        Update multiple feature flags at once.

        Args:
            tenant_id: Tenant UUID
            flags: Dictionary of feature flags to update

        Returns:
            Updated Tenant entity

        Raises:
            ModelNotFoundError: If tenant doesn't exist
        """
        tenant = await self._get_tenant_for_update(tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None:
            tenant.feature_flags = {}
        tenant.feature_flags.update(flags)
        flag_modified(tenant, "feature_flags")

        return await self._commit_and_invalidate(tenant, tenant_id, list(flags))

    # =========================================================================
    # Private Helpers
    # =========================================================================

    async def _get_tenant_for_update(self, tenant_id: UUID) -> Tenant | None:
        """Read the Tenant row under a ``SELECT ... FOR UPDATE`` row lock.

        The three tenant-JSONB mutators are a read-modify-write on the
        WHOLE ``feature_flags`` column. With an unlocked read, two
        concurrent ``update_tenant_flag`` calls both load the same dict and
        the second commit silently discards the first flag; ``delete_tenant_flag``
        has the same window as check-then-act.

        Locking on the primary key is deliberate: ``AsyncSession.get`` skips
        the SELECT entirely when the row is already in the identity map,
        which is the common case in a request (the tenant was resolved from
        the path/header before the service ran), so a plain ``db.get`` would
        take NO row lock in production. Passing ``with_for_update`` makes the
        statement emit a real ``FOR UPDATE`` and is held until the mutator's
        own commit — the same transaction, no second mechanism.
        """
        return await self.db.get(Tenant, tenant_id, with_for_update=True)

    async def _commit_and_invalidate(self, tenant: Tenant, tenant_id: UUID, feature_names: list[str]) -> Tenant:
        """Commit a tenant-JSONB write and evict its decision keys.

        Runs under ``_flag_cache_locks[features]`` — the SAME singleflight
        lock ``get_tenant_flags``'s ``_load()`` already holds across its
        ``db.get`` + ``cache_client.set``. Without it the two interleave:
        reader loads PRE-commit JSONB, writer commits and deletes the key,
        reader then writes the PRE-commit map back — stale and un-evictable
        for CACHE_TTL. Same lock table, same key, no second mechanism.

        Lock order is ``features`` alone, which keeps the graph acyclic:
        ``get_flag_value`` nests ``feature_value:{name}`` -> ``features``
        (never the reverse) and ``is_enabled``'s ``_load`` reads Tenant
        directly and takes no further lock. The row lock is already held
        here and is released by the commit below, so no holder of the
        singleflight lock is ever waiting on a second one.
        """

        async def _run() -> None:
            await self.db.commit()
            await self.db.refresh(tenant)
            await self._invalidate_decision_keys(tenant_id, feature_names)

        await _with_flag_lock(build_tenant_key("features", tenant_id), _run())
        return tenant

    async def _invalidate_decision_keys(self, tenant_id: UUID, feature_names: Sequence[str]) -> None:
        """Evict every decision key a tenant-JSONB write can leave stale.

        ONE varargs delete per call (a single round trip, P2). The three
        shapes are exactly what the accessors read: the whole map
        (``features``), the boolean decision (``feature:{name}``) and the
        typed decision (``feature_value:{name}``, F27). The single-flag and
        bulk paths both come through here so they cannot drift apart again
        — the bulk path previously omitted ``feature_value:`` and left
        ``get_flag_value`` serving the pre-bulk value for CACHE_TTL.
        """
        try:
            keys = [build_tenant_key("features", tenant_id)]
            for name in feature_names:
                keys.append(build_tenant_key(f"feature:{name}", tenant_id))
                keys.append(build_tenant_key(f"feature_value:{name}", tenant_id))
            await cache_client.delete(*keys)
        except Exception as e:
            # Q-M3: failed invalidation leaves stale tenant flags until TTL —
            # warn with tenant + flag identifiers so operators can flush.
            logger.warning(
                "Tenant feature flag cache invalidation failed",
                tenant_id=str(tenant_id),
                feature_name=feature_names[0] if len(feature_names) == 1 else None,
                count=len(feature_names),
                error=str(e),
            )

    async def _invalidate_cache(self, tenant_id: UUID, feature_name: str | None = None) -> None:
        """Invalidate cache for tenant feature flags."""
        await self._invalidate_decision_keys(tenant_id, [feature_name] if feature_name else [])

    async def _purge_registry_caches(self, flag_key: str) -> None:
        """Purge one registry row's per-tenant decisions fleet-wide (F1).

        ``update_flag``/``delete_flag`` change what every tenant resolves,
        but decisions are cached under ``tenant:{tid}:...`` keys this
        service cannot enumerate from the adapter. SCAN the three key
        shapes directly on the raw client; a down Redis no-ops (fail-open,
        entries expire via TTL).
        """
        client = cache_client.client
        if client is None:
            return
        try:
            for pattern in (
                f"tenant:*:feature:{flag_key}",
                f"tenant:*:feature_value:{flag_key}",
                "tenant:*:features",
            ):
                cursor = 0
                while True:
                    cursor, keys = await client.scan(cursor, match=pattern, count=500)
                    if keys:
                        await client.delete(*keys)
                    if cursor == 0:
                        break
        except Exception as e:
            logger.warning(
                "Feature flag registry purge failed",
                flag_key=flag_key,
                error=str(e),
            )


# ============================================================================
# Cache Warming
# ============================================================================


@warmer.register("feature_flags")
async def warm_feature_flags(session: AsyncSession) -> int:
    """Warm feature flags cache.

    Fetches all feature flags and stores them in Redis cache.
    Low-cardinality, high-read data perfect for caching.

    Args:
        session: Database session

    Returns:
        Number of flags cached
    """
    from sqlalchemy import select

    from src.app.modules.feature_flags.models import FeatureFlag

    query = select(FeatureFlag)
    result = await session.execute(query)
    flags = result.scalars().all()

    cached_count = 0
    # Pipeline bulk write keeps the raw client on purpose: tests patch the
    # provider seam, and the pipeline API has no adapter equivalent.
    redis_client = get_redis()
    if redis_client and flags:
        try:
            pipe = redis_client.pipeline()
            for flag in flags:
                cache_key = f"feature_flag:{flag.key}"
                # F28: warm every constructor-accepted column, not a subset.
                # Server-generated fields (uuid/created_at/updated_at) and
                # relationships are init=False, so a warmed dict can never
                # carry them — readers get a detached registry row and must
                # only use registry fields (key/id/default_value/value_type/
                # flag_metadata/is_system/description).
                flag_data = {
                    "id": flag.id,
                    "key": flag.key,
                    "description": flag.description,
                    "default_value": flag.default_value,
                    "value_type": flag.value_type,
                    "flag_metadata": flag.flag_metadata or {},
                    "is_system": flag.is_system,
                }
                pipe.setex(cache_key, CACHE_TTL, json_dumps(flag_data, as_str=True))
            await pipe.execute()
            cached_count = len(flags)

            logger.info("Warmed cache for feature flags", cached_count=cached_count)
        except Exception as e:
            logger.error("Failed to warm feature flags cache", error=e)
            return 0

    return cached_count
