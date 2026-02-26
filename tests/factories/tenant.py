"""
Tenant Factory

Creates Tenant model instances for testing with automatic slug generation.
"""

from datetime import UTC, datetime

from src.app.modules.tenants.models import Tenant
from tests.factories.base import AsyncFactory, random_string


class TenantFactory(AsyncFactory):
    """Factory for creating Tenant test instances.

    Usage:
        tenant = await TenantFactory.create_async(db=session)
        tenant = await TenantFactory.create_async(db=session, name="Acme Corp", slug="acme")
    """

    model = Tenant
    defaults = {
        "name": lambda: f"Tenant_{random_string(length=6)}",
        "slug": lambda: f"tenant-{random_string(length=8).lower()}",
        "is_active": True,
        "feature_flags": lambda: {},
        "storage_usage_bytes": 0,
        "file_count": 0,
        "created_at": lambda: datetime.now(UTC),
        "updated_at": lambda: datetime.now(UTC),
    }

    @classmethod
    async def create_with_tier(cls, db, tier=None, **kwargs):
        """Create a tenant with an associated tier.

        Args:
            db: Async SQLAlchemy session
            tier: Optional Tier instance (creates one if not provided)
            **kwargs: Additional tenant attributes

        Returns:
            Tenant instance
        """
        from tests.factories.tier import TierFactory

        if tier is None:
            tier = await TierFactory.create_async(db)

        # Note: Tenant doesn't have direct tier_id, but we return both
        tenant = await cls.create_async(db, **kwargs)
        return tenant, tier
