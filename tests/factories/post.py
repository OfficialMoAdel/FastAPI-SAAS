"""
Post Factory

Creates Post model instances for testing.
"""

from uuid import uuid4

from tests.factories.base import AsyncFactory, random_string
from src.app.modules.posts.models import Post


class PostFactory(AsyncFactory):
    """Factory for creating Post test instances.

    Usage:
        post = await PostFactory.create_async(
            db=session,
            tenant_id=1,
            user_id=1,
            created_by_user_id=1
        )
    """

    model = Post
    defaults = {
        "title": lambda: f"Post Title {random_string(length=6)}",
        "text": lambda: f"This is the content of the post. {random_string(length=20)}",
        "uuid": lambda: uuid4(),
        "is_deleted": False,
        "media_url": None,
    }

    @classmethod
    async def create_with_user(cls, db, user=None, **kwargs):
        """Create a post with an associated user.

        Args:
            db: Async SQLAlchemy session
            user: Optional User instance (creates one if not provided)
            **kwargs: Additional post attributes

        Returns:
            Post instance
        """
        from tests.factories.user import UserFactory
        from tests.factories.tenant import TenantFactory

        if user is None:
            tenant = await TenantFactory.create_async(db)
            user = await UserFactory.create_async(db, tenant_id=tenant.id)

        return await cls.create_async(
            db, tenant_id=user.tenant_id, user_id=user.id, created_by_user_id=user.id, **kwargs
        )
