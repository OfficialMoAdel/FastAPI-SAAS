"""Feature flags utility functions."""

import warnings
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client
from src.app.modules._base import ModelNotFoundError
from src.app.modules.tenants.models import Tenant

logger = get_logger(__name__)

CACHE_TTL = 300  # 5 minutes


async def is_feature_enabled(db: AsyncSession, tenant_id: UUID, feature_name: str, default: bool = False) -> bool:
    """
    Check if a feature flag is enabled for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        feature_name: Name of the feature flag
        default: Default value if flag is not set

    Returns
    -------
        bool: True if feature is enabled, False otherwise
    """
    warnings.warn(
        "is_feature_enabled is deprecated, use FeatureFlagService.is_enabled() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    cache_key = f"tenant:{tenant_id}:feature:{feature_name}"

    # Try cache first
    if redis_client:
        try:
            cached_value = await redis_client.get(cache_key)
            if cached_value is not None:
                # Redis may return bytes or str depending on decode_responses setting
                val_str: str = (cached_value.decode() if isinstance(cached_value, bytes) else cached_value).lower()
                return val_str == "true"
        except Exception as e:
            logger.debug("Cache get failed", error=e)

    # Fallback to DB
    result = await db.get(Tenant, tenant_id)
    enabled = default

    if result and result.feature_flags:
        enabled = bool(result.feature_flags.get(feature_name, default))

    # Update cache
    if redis_client:
        try:
            await redis_client.setex(cache_key, CACHE_TTL, str(enabled).lower())
        except Exception as e:
            logger.debug("Cache operation failed", error=e)

    return enabled


async def get_feature_flags(db: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """
    Get all feature flags for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID

    Returns
    -------
        dict: All feature flags for the tenant
    """
    warnings.warn(
        "get_feature_flags is deprecated, use FeatureFlagService.get_tenant_flags() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    cache_key = f"tenant:{tenant_id}:features"

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
    result = await db.get(Tenant, tenant_id)
    flags = {}

    if result and result.feature_flags:
        flags = result.feature_flags

    # Update cache
    if redis_client:
        try:
            await redis_client.setex(cache_key, CACHE_TTL, json_dumps(flags, as_str=True))
        except Exception as e:
            logger.debug("Cache operation failed", error=e)

    return flags


async def update_feature_flag(db: AsyncSession, tenant_id: UUID, feature_name: str, enabled: bool) -> Tenant:
    """
    Update a single feature flag for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        feature_name: Name of the feature flag
        enabled: Whether to enable or disable the feature

    Returns
    -------
        Tenant: Updated tenant object


    DEPRECATED: Use FeatureFlagService.update_tenant_flag() instead.

    Raises
    ------
        ModelNotFoundError: If tenant doesn't exist
    """
    warnings.warn(
        "update_feature_flag is deprecated, use FeatureFlagService.update_tenant_flag() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ModelNotFoundError("Tenant", tenant_id)

    # Initialize feature_flags if None
    if tenant.feature_flags is None:
        tenant.feature_flags = {}

    # Update the feature flag
    tenant.feature_flags[feature_name] = enabled

    # Mark as modified for SQLAlchemy to detect change
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(tenant, "feature_flags")

    await db.commit()
    await db.refresh(tenant)

    # Invalidate cache
    if redis_client:
        try:
            # Delete specific feature key
            await redis_client.delete(f"tenant:{tenant_id}:feature:{feature_name}")
            # Delete all features key
            await redis_client.delete(f"tenant:{tenant_id}:features")
        except Exception as e:
            logger.debug("Cache invalidation failed", error=e)

    return tenant


async def bulk_update_feature_flags(db: AsyncSession, tenant_id: UUID, flags: dict[str, bool]) -> Tenant:
    """
    Update multiple feature flags at once.

    DEPRECATED: Use FeatureFlagService.bulk_update_tenant_flags() instead.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        flags: Dictionary of feature flags to update

    Returns
    -------
        Tenant: Updated tenant object

    Raises
    ------
        ModelNotFoundError: If tenant doesn't exist
    """
    warnings.warn(
        "bulk_update_feature_flags is deprecated, use FeatureFlagService.bulk_update_tenant_flags() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ModelNotFoundError("Tenant", tenant_id)

    # Initialize feature_flags if None
    if tenant.feature_flags is None:
        tenant.feature_flags = {}

    # Update all flags
    tenant.feature_flags.update(flags)

    # Mark as modified for SQLAlchemy to detect change
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(tenant, "feature_flags")

    await db.commit()
    await db.refresh(tenant)

    # Invalidate cache
    if redis_client:
        try:
            # Delete all features key
            await redis_client.delete(f"tenant:{tenant_id}:features")

            # Delete individual keys for updated flags
            keys_to_delete = [f"tenant:{tenant_id}:feature:{k}" for k in flags]
            if keys_to_delete:
                await redis_client.delete(*keys_to_delete)
        except Exception as e:
            logger.debug("Cache invalidation failed", error=e)

    return tenant
