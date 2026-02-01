"""
API Key Factory

Creates APIKey model instances for testing.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.factories.base import AsyncFactory, random_string
from src.app.modules.api_keys.models import APIKey


class APIKeyFactory(AsyncFactory):
    """Factory for creating APIKey test instances.

    Usage:
        api_key = await APIKeyFactory.create_async(
            db=session,
            tenant_id=1,
            user_id=1
        )
    """

    model = APIKey
    defaults = {
        "name": lambda: f"API Key {random_string(length=6)}",
        "hashed_key": lambda: secrets.token_hex(32),
        "prefix": lambda: f"sk_{random_string(length=4)}",
        "uuid": lambda: uuid4(),
        "is_active": True,
        "expires_at": lambda: datetime.now(UTC) + timedelta(days=365),
        "scopes": lambda: ["read", "write"],
    }

    @classmethod
    async def create_with_user(cls, db, user=None, **kwargs):
        """Create an API key with an associated user.

        Args:
            db: Async SQLAlchemy session
            user: Optional User instance (creates one if not provided)
            **kwargs: Additional API key attributes

        Returns:
            APIKey instance
        """
        from tests.factories.user import UserFactory
        from tests.factories.tenant import TenantFactory

        if user is None:
            tenant = await TenantFactory.create_async(db)
            user = await UserFactory.create_async(db, tenant_id=tenant.id)

        return await cls.create_async(db, tenant_id=user.tenant_id, user_id=user.id, **kwargs)

    @classmethod
    async def create_expired(cls, db, tenant_id: int, user_id: int, **kwargs):
        """Create an expired API key for testing expiration logic.

        Args:
            db: Async SQLAlchemy session
            tenant_id: Tenant ID
            user_id: User ID
            **kwargs: Additional API key attributes

        Returns:
            APIKey instance with past expiration date
        """
        return await cls.create_async(
            db, tenant_id=tenant_id, user_id=user_id, expires_at=datetime.now(UTC) - timedelta(days=1), **kwargs
        )
