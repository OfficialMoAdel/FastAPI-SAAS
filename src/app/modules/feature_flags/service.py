"""Feature flags service with BaseService pattern."""

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar, cast

T = TypeVar("T")
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.app.core.cache.warmer import warmer
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules._base import BaseService, ModelNotFoundError
from src.app.modules.feature_flags.crud import crud_feature_flag_overrides, crud_feature_flags
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.schemas import (
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideUpdate,
    FeatureFlagUpdate,
)
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

CACHE_TTL = 300  # 5 minutes

# P2.19 FIX: per-key singleflight locks for the manual feature-flag caches.
# On a cold key under burst traffic, N concurrent requests would each miss
# the cache and hit the DB (classic cache stampede). These locks serialize
# the cache-miss path so only one coroutine per key loads from the DB; the
# others wait and then read the freshly-written cache value.
_flag_cache_locks: dict[str, asyncio.Lock] = {}


async def _with_flag_lock(key: str, coro: Awaitable[T]) -> T:
    """Run `coro` under a per-key lock (singleflight)."""
    import asyncio

    lock = _flag_cache_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _flag_cache_locks[key] = lock
    async with lock:
        return await coro


class FeatureFlagService(BaseService[FeatureFlag]):
    """Service for managing feature flags using BaseService pattern."""

    def __init__(self, db: AsyncSession):
        super().__init__(model=FeatureFlag, db=db)
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
        redis_client = get_redis()
        if redis_client:
            try:
                cache_key = f"feature_flag:{key}"
                cached = await redis_client.get(cache_key)
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
        await self.update(flag_id, **data.model_dump(exclude_unset=True))
        flag = await self.crud.get(db=self.db, id=flag_id)
        if flag is None:
            raise ModelNotFoundError("FeatureFlag", flag_id)

        flag_key = flag.key if not isinstance(flag, dict) else flag.get("key")
        redis_client = get_redis()
        if redis_client and flag_key:
            try:
                await redis_client.delete(f"feature_flag:{flag_key}")
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
            redis_client = get_redis()
            if redis_client and flag_key:
                try:
                    await redis_client.delete(f"feature_flag:{flag_key}")
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
        # TenantAwareCRUD.update is typed as returning None, but FastCRUD's
        # update actually returns the updated row at runtime. Cast to the real type.
        result = cast(
            "FeatureFlagOverride | None",
            await self.override_crud.update(db=self.db, id=override_id, object=data.model_dump(exclude_unset=True)),
        )
        if result is None:
            return None
        override = result

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
        from src.app.core.utils.cache import build_tenant_key

        cache_key = build_tenant_key("features", tenant_id)
        redis_client = get_redis()

        # Fast path: serve from cache if present.
        if redis_client:
            try:
                cached_value = await redis_client.get(cache_key)
                if cached_value:
                    return cast("dict[str, Any]", json_loads(cached_value))
            except Exception as e:
                # Q-M3: cache read failure serves DB-default flags while degraded.
                logger.warning("Feature flag tenant cache get failed", cache_key=cache_key, error=str(e))

        async def _load() -> dict[str, Any]:
            # P2.19 FIX: re-check cache now that we hold the per-key lock,
            # so a concurrent caller that already populated it is served from cache
            # instead of stampeding the DB.
            if redis_client:
                try:
                    cached_value = await redis_client.get(cache_key)
                    if cached_value:
                        return cast("dict[str, Any]", json_loads(cached_value))
                except Exception as e:
                    # Q-M3: cache read failure serves DB flags while degraded.
                    logger.warning("Tenant feature flags cache get failed", cache_key=cache_key, error=str(e))

            tenant = await self.db.get(Tenant, tenant_id)
            flags: dict[str, Any] = {}
            if tenant and tenant.feature_flags:
                flags = tenant.feature_flags

            if redis_client:
                try:
                    await redis_client.setex(cache_key, CACHE_TTL, json_dumps(flags, as_str=True))
                except Exception as e:
                    # Q-M3: cache write failure means every read pays the DB cost
                    # until recovery — warn, don't hide at debug.
                    logger.warning("Tenant feature flags cache write failed", cache_key=cache_key, error=str(e))
            return flags

        return await _with_flag_lock(cache_key, _load())

    async def is_enabled(self, tenant_id: UUID, feature_name: str, default: bool = False) -> bool:
        """
        Check if a feature is enabled for a tenant.

        Args:
            tenant_id: Tenant UUID
            feature_name: Name of the feature flag
            default: Default value if flag is not set

        Returns:
            True if feature is enabled, False otherwise
        """
        from src.app.core.utils.cache import build_tenant_key

        cache_key = build_tenant_key(f"feature:{feature_name}", tenant_id)
        redis_client = get_redis()

        # Fast path: serve from cache if present.
        if redis_client:
            try:
                cached_value = await redis_client.get(cache_key)
                if cached_value is not None:
                    # BUG FIX: .decode() crashes when Redis has decode_responses=True
                    # (value is already a str). Use a safe conversion for both cases.
                    val_str: str = (cached_value.decode() if isinstance(cached_value, bytes) else cached_value).lower()
                    return val_str == "true"
            except Exception as e:
                # Q-M3: cache read failure serves DB-default flags while degraded.
                logger.warning("Feature flag tenant cache get failed", cache_key=cache_key, error=str(e))

        async def _load() -> bool:
            # P2.19 FIX: re-check cache under the per-key lock (singleflight)
            # so concurrent cache misses don't all hit the DB.
            if redis_client:
                try:
                    cached_value = await redis_client.get(cache_key)
                    if cached_value is not None:
                        val_str = (cached_value.decode() if isinstance(cached_value, bytes) else cached_value).lower()
                        return val_str == "true"
                except Exception as e:
                    # Q-M3: cache read failure serves DB-default flags while degraded.
                    logger.warning("Feature flag enabled-state cache get failed", cache_key=cache_key, error=str(e))

            # Fallback to DB (H-12 FIX: Check flag and overrides first)
            enabled = default

            flag = await self.get_flag_by_key_or_none(feature_name)
            if flag:
                flag_id = int(get_user_attr(flag, "id", 0))
                flag_default = bool(get_user_attr(flag, "default_value", False))

                override = await self.get_override(flag_id, tenant_id)

                if override:
                    custom_value = get_user_attr(override, "custom_value")
                    if custom_value is not None:
                        if isinstance(custom_value, dict):
                            if "value" in custom_value:
                                enabled = bool(custom_value["value"])
                            elif "enabled" in custom_value:
                                enabled = bool(custom_value["enabled"])
                            else:
                                enabled = bool(custom_value)
                        else:
                            enabled = bool(custom_value)
                    else:
                        enabled = flag_default
                else:
                    enabled = flag_default
            else:
                tenant = await self.db.get(Tenant, tenant_id)
                if tenant and tenant.feature_flags:
                    enabled = bool(tenant.feature_flags.get(feature_name, default))

            if redis_client:
                try:
                    await redis_client.setex(cache_key, CACHE_TTL, str(enabled).lower())
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
        tenant = await self.db.get(Tenant, tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None:
            tenant.feature_flags = {}
        tenant.feature_flags[feature_name] = value
        flag_modified(tenant, "feature_flags")

        await self.db.commit()
        await self.db.refresh(tenant)

        # Invalidate cache
        await self._invalidate_cache(tenant_id, feature_name)

        return tenant

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
        tenant = await self.db.get(Tenant, tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None or flag_name not in tenant.feature_flags:
            raise ModelNotFoundError("FeatureFlag", flag_name)
        del tenant.feature_flags[flag_name]
        flag_modified(tenant, "feature_flags")

        await self.db.commit()
        await self.db.refresh(tenant)

        # Invalidate cache
        await self._invalidate_cache(tenant_id, flag_name)

        return tenant

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
        tenant = await self.db.get(Tenant, tenant_id)
        if not tenant:
            raise ModelNotFoundError("Tenant", tenant_id)

        if tenant.feature_flags is None:
            tenant.feature_flags = {}
        tenant.feature_flags.update(flags)
        flag_modified(tenant, "feature_flags")

        await self.db.commit()
        await self.db.refresh(tenant)

        # Invalidate cache for all updated flags
        await self._invalidate_cache(tenant_id)
        for flag_name in flags.keys():
            await self._invalidate_cache(tenant_id, feature_name=flag_name)

        return tenant

    # =========================================================================
    # Private Helpers
    # =========================================================================

    async def _invalidate_cache(self, tenant_id: UUID, feature_name: str | None = None) -> None:
        """Invalidate cache for tenant feature flags."""
        redis_client = get_redis()
        if not redis_client:
            return

        try:
            from src.app.core.utils.cache import build_tenant_key

            await redis_client.delete(build_tenant_key("features", tenant_id))
            if feature_name:
                await redis_client.delete(build_tenant_key(f"feature:{feature_name}", tenant_id))
        except Exception as e:
            # Q-M3: failed invalidation leaves stale tenant flags until TTL —
            # warn with tenant + flag identifiers so operators can flush.
            logger.warning(
                "Tenant feature flag cache invalidation failed",
                tenant_id=str(tenant_id),
                feature_name=feature_name,
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
    redis_client = get_redis()
    if redis_client:
        try:
            for flag in flags:
                cache_key = f"feature_flag:{flag.key}"
                flag_data = {
                    "id": flag.id,
                    "key": flag.key,
                    "description": flag.description,
                    "default_value": flag.default_value,
                    "value_type": flag.value_type,
                    "is_system": flag.is_system,
                }
                await redis_client.setex(cache_key, CACHE_TTL, json_dumps(flag_data, as_str=True))
                cached_count += 1

            logger.info("Warmed cache for feature flags", cached_count=cached_count)
        except Exception as e:
            logger.error("Failed to warm feature flags cache", error=e)
            return 0

    return cached_count
