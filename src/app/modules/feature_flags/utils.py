"""Feature flags utility functions."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.tenants.models import Tenant


async def is_feature_enabled(db: AsyncSession, tenant_id: UUID, feature_name: str, default: bool = False) -> bool:
    """
    Check if a feature flag is enabled for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        feature_name: Name of the feature flag
        default: Default value if flag is not set

    Returns:
        bool: True if feature is enabled, False otherwise

    Example:
        >>> if await is_feature_enabled(db, tenant.id, "advanced_analytics"):
        >>>     # Show advanced analytics
    """
    result = await db.get(Tenant, tenant_id)
    if not result:
        return default

    if not result.feature_flags:
        return default

    return result.feature_flags.get(feature_name, default)


async def get_feature_flags(db: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """
    Get all feature flags for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID

    Returns:
        dict: All feature flags for the tenant

    Example:
        >>> flags = await get_feature_flags(db, tenant.id)
        >>> print(flags)
        {"advanced_analytics": True, "api_access": False}
    """
    result = await db.get(Tenant, tenant_id)
    if not result:
        return {}

    return result.feature_flags or {}


async def update_feature_flag(db: AsyncSession, tenant_id: UUID, feature_name: str, enabled: bool) -> Tenant:
    """
    Update a single feature flag for a tenant.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        feature_name: Name of the feature flag
        enabled: Whether to enable or disable the feature

    Returns:
        Tenant: Updated tenant object

    Example:
        >>> tenant = await update_feature_flag(
        >>>     db, tenant.id, "advanced_analytics", True
        >>> )
    """
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

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

    return tenant


async def bulk_update_feature_flags(db: AsyncSession, tenant_id: UUID, flags: dict[str, bool]) -> Tenant:
    """
    Update multiple feature flags at once.

    Args:
        db: Database session
        tenant_id: Tenant UUID
        flags: Dictionary of feature flags to update

    Returns:
        Tenant: Updated tenant object

    Example:
        >>> tenant = await bulk_update_feature_flags(db, tenant.id, {
        >>>     "advanced_analytics": True,
        >>>     "api_access": True,
        >>>     "custom_branding": False
        >>> })
    """
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

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

    return tenant
