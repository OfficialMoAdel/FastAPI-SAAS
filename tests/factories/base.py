"""
Base Factory Class for Async SQLAlchemy Models

Provides a simple factory pattern that works with async SQLAlchemy sessions.
"""

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class AsyncFactory:
    """Base factory class for creating test model instances.

    Usage:
        class UserFactory(AsyncFactory):
            model = User
            defaults = {
                "name": lambda: f"User {uuid4().hex[:8]}",
                "email": lambda: f"user_{uuid4().hex[:8]}@example.com",
            }

        user = await UserFactory.create_async(db=session, name="Custom Name")
    """

    model: type[Any] = None  # type: ignore
    defaults: dict[str, Any] = {}

    @classmethod
    def _resolve_value(cls, value: Any) -> Any:
        """Resolve a value - if it's callable, call it."""
        if callable(value):
            return value()
        return value

    @classmethod
    def build(cls, **kwargs: Any) -> dict[str, Any]:
        """Build a dictionary of model attributes without saving."""
        data = {}
        for key, value in cls.defaults.items():
            data[key] = cls._resolve_value(value)
        data.update(kwargs)
        return data

    @classmethod
    async def create_async(cls, db: AsyncSession, **kwargs: Any) -> Any:
        """Create and persist a model instance.

        Args:
            db: Async SQLAlchemy session
            **kwargs: Override default values

        Returns:
            The created model instance
        """
        if cls.model is None:
            raise ValueError(f"Factory {cls.__name__} must define 'model' attribute")

        data = cls.build(**kwargs)

        # Create model instance
        instance = cls.model(**data)

        # Add to session and flush to get ID
        db.add(instance)
        await db.flush()
        await db.refresh(instance)

        return instance

    @classmethod
    async def create_batch_async(cls, db: AsyncSession, count: int, **kwargs: Any) -> list[Any]:
        """Create multiple model instances.

        Args:
            db: Async SQLAlchemy session
            count: Number of instances to create
            **kwargs: Override default values (applied to all)

        Returns:
            List of created model instances
        """
        instances = []
        for _ in range(count):
            instance = await cls.create_async(db, **kwargs)
            instances.append(instance)
        return instances


# Common value generators
def random_string(prefix: str = "", length: int = 8) -> str:
    """Generate a random string with optional prefix."""
    return f"{prefix}{uuid4().hex[:length]}"


def random_email(domain: str = "example.com") -> str:
    """Generate a random email address."""
    return f"user_{uuid4().hex[:8]}@{domain}"


def now_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)
