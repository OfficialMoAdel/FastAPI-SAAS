"""
User Factory

Creates User model instances for testing with automatic relationship handling.
"""

from uuid import uuid4

from src.app.modules.users.models import User
from tests.factories.base import AsyncFactory, random_email, random_string


class UserFactory(AsyncFactory):
    """Factory for creating User test instances.

    Usage:
        user = await UserFactory.create_async(db=session, tenant_id=1)
        user = await UserFactory.create_async(
            db=session,
            tenant_id=1,
            name="John Doe",
            email="john@example.com"
        )
    """

    model = User
    defaults = {
        "name": lambda: f"User_{random_string(length=6)}",
        "username": lambda: f"user_{random_string(length=8).lower()}",
        "email": lambda: random_email(),
        "hashed_password": lambda: f"hashed_{uuid4().hex}",
        "profile_image_url": "https://example.com/avatar.png",
        "is_deleted": False,
        "is_superuser": False,
        "is_email_verified": True,
    }

    @classmethod
    async def create_with_tenant(cls, db, tenant=None, **kwargs):
        """Create a user with an associated tenant.

        Args:
            db: Async SQLAlchemy session
            tenant: Optional Tenant instance (creates one if not provided)
            **kwargs: Additional user attributes

        Returns:
            User instance
        """
        from tests.factories.tenant import TenantFactory

        if tenant is None:
            tenant = await TenantFactory.create_async(db)

        return await cls.create_async(db, tenant_id=tenant.id, **kwargs)

    @classmethod
    async def create_superuser(cls, db, tenant_id: int, **kwargs):
        """Create a superuser.

        Args:
            db: Async SQLAlchemy session
            tenant_id: Tenant ID to associate with
            **kwargs: Additional user attributes

        Returns:
            User instance with is_superuser=True
        """
        return await cls.create_async(db, tenant_id=tenant_id, is_superuser=True, **kwargs)
