"""Feature flags service with BaseService pattern."""

from typing import Any
from uuid import UUID

from fastapi import Depends
from fastcrud import FastCRUD
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.app.core.cache.warmer import warmer
from src.app.core.db.database import async_get_db
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client
from src.app.modules._base import BaseService, ModelNotFoundError
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


class FeatureFlagService(BaseService[FeatureFlag]):
    """Service for managing feature flags using BaseService pattern."""

    def __init__(self, db: AsyncSession):
        super().__init__(model=FeatureFlag, db=db)
        self.override_crud: FastCRUD[FeatureFlagOverride, Any, Any, Any, Any, Any] = FastCRUD(FeatureFlagOverride)

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
                logger.debug("Feature flag cache get failed", error=str(e))

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
        return await self.create(**data.model_dump())

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
        flag = await self.update(flag_id, **data.model_dump(exclude_unset=True))
        if not flag:
            raise ModelNotFoundError("FeatureFlag", flag_id)

        flag_key = flag.key if not isinstance(flag, dict) else flag.get("key")
        if redis_client and flag_key:
            try:
                await redis_client.delete(f"feature_flag:{flag_key}")
            except Exception as e:
                logger.debug("Feature flag cache invalidation failed", error=str(e))

        return flag

    async def delete_flag(self, flag_id: int) -> None:
        """
        Delete a feature flag.

        Args:
            flag_id: ID of the flag to delete
        """
        result = await self.crud.get(db=self.db, id=flag_id)
        if result:
            flag_key = result.key if not isinstance(result, dict) else result.get("key")
            if redis_client and flag_key:
                try:
                    await redis_client.delete(f"feature_flag:{flag_key}")
                except Exception as e:
                    logger.debug("Feature flag cache invalidation failed", error=str(e))
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
        """Update a feature flag override."""
        result = await self.override_crud.update(db=self.db, id=override_id, object=data.model_dump(exclude_unset=True))
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
        from src.app.core.utils.cache import build_tenant_key
        cache_key = build_tenant_key("features", tenant_id)

        # Try cache first
        if redis_client:
            try:
                cached_value = await redis_client.get(cache_key)
                if cached_value:
                    result_data: dict[str, Any] = json_loads(cached_value)
                    return result_data
            except Exception as e:
                logger.debug("Cache get failed", error=e)

        # Fallback to DB
        tenant = await self.db.get(Tenant, tenant_id)
        flags: dict[str, Any] = {}

        if tenant and tenant.feature_flags:
            flags = tenant.feature_flags

        # Update cache
        if redis_client:
            try:
                await redis_client.setex(cache_key, CACHE_TTL, json_dumps(flags, as_str=True))
            except Exception as e:
                logger.debug("Cache operation failed", error=e)

        return flags

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

        # Try cache first
        if redis_client:
            try:
                cached_value = await redis_client.get(cache_key)
                if cached_value is not None:
                    # BUG FIX: .decode() crashes when Redis has decode_responses=True
                    # (value is already a str). Use a safe conversion for both cases.
                    val_str: str = (cached_value.decode() if isinstance(cached_value, bytes) else cached_value).lower()
                    return val_str == "true"
            except Exception as e:
                logger.debug("Cache get failed", error=e)

        # Fallback to DB (H-12 FIX: Check flag and overrides first)
        enabled = default

        flag = await self.get_flag_by_key_or_none(feature_name)
        if flag:
            flag_id = int(flag.id if not isinstance(flag, dict) else flag.get("id", 0))
            flag_default = bool(flag.default_value if not isinstance(flag, dict) else flag.get("default_value", False))

            override = await self.get_override(flag_id, tenant_id)

            if override:
                custom_value = override.custom_value if not isinstance(override, dict) else override.get("custom_value")
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

        # Update cache
        if redis_client:
            try:
                await redis_client.setex(cache_key, CACHE_TTL, str(enabled).lower())
            except Exception as e:
                logger.debug("Cache operation failed", error=e)

        return enabled

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

        # Initialize feature_flags if None
        if tenant.feature_flags is None:
            tenant.feature_flags = {}

        # Update the feature flag
        tenant.feature_flags[feature_name] = value

        # Mark as modified for SQLAlchemy to detect change
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

        # Remove flag
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

        # Initialize feature_flags if None
        if tenant.feature_flags is None:
            tenant.feature_flags = {}

        # Update all flags
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
        if not redis_client:
            return

        try:
            from src.app.core.utils.cache import build_tenant_key
            await redis_client.delete(build_tenant_key("features", tenant_id))
            if feature_name:
                await redis_client.delete(build_tenant_key(f"feature:{feature_name}", tenant_id))
        except Exception as e:
            logger.debug("Cache invalidation failed", error=e)


# =============================================================================
# Dependency Injection Factory
# =============================================================================


def get_feature_flag_service(
    db: AsyncSession = Depends(async_get_db),
) -> FeatureFlagService:
    """
    Dependency injection factory for FeatureFlagService.

    Usage:
        @router.get("/flags")
        async def list_flags(
            service: FeatureFlagService = Depends(get_feature_flag_service)
        ):
            ...
    """
    return FeatureFlagService(db=db)


# =============================================================================
# Legacy Compatibility (Backward Compatible)
# =============================================================================


class _LegacyFeatureFlagsService:
    """
    Legacy wrapper for backward compatibility.

    DEPRECATED: Use get_feature_flag_service() dependency injection instead.
    """

    def __init__(self) -> None:
        self._service: FeatureFlagService | None = None

    async def _get_service(self, db: AsyncSession) -> FeatureFlagService:
        """Get or create service instance with db session."""
        return FeatureFlagService(db=db)

    async def get_flag(self, db: AsyncSession, name: str, tenant_id: UUID) -> FeatureFlag | None:
        service = await self._get_service(db)
        return await service.get_flag_by_key_or_none(name)

    async def is_feature_enabled(self, db: AsyncSession, flag_name: str, user_id: int | None, tenant_id: UUID) -> bool:
        service = await self._get_service(db)
        return await service.is_enabled(UUID(str(tenant_id)), flag_name, default=False)


# Legacy singleton for backward compatibility
feature_flags_service = _LegacyFeatureFlagsService()


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
