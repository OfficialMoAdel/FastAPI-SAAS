"""
Database Seeding Framework - Factory Classes

Provides factory-based test data generation using factory_boy patterns.
All factories support async SQLAlchemy operations.

Usage:
    from tests.factories import UserFactory, TenantFactory, TierFactory

    # Create with defaults
    user = await UserFactory.create_async(db=session)

    # Create with overrides
    user = await UserFactory.create_async(db=session, name="Test User")

    # Create with relationships
    user = await UserFactory.create_async(
        db=session,
        tenant=await TenantFactory.create_async(db=session, name="My Tenant")
    )
"""

from tests.factories.base import AsyncFactory
from tests.factories.tier import TierFactory
from tests.factories.tenant import TenantFactory
from tests.factories.user import UserFactory
from tests.factories.post import PostFactory
from tests.factories.api_key import APIKeyFactory

__all__ = [
    "AsyncFactory",
    "TierFactory",
    "TenantFactory",
    "UserFactory",
    "PostFactory",
    "APIKeyFactory",
]
