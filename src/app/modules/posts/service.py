"""Post service layer - encapsulates all post business logic."""

from typing import Any

from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ForbiddenException
from ...core.tenancy.context import get_tenant_context
from .._base.exceptions import ModelNotFoundError
from .._base.service import BaseService
from .crud import crud_posts
from .models import Post
from .schemas import PostCreate, PostCreateInternal, PostRead, PostUpdate


class PostService(BaseService[Post]):
    """Service class for post-related business logic."""

    def __init__(self, db: AsyncSession):
        """Initialize PostService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(Post, db)

    async def _check_ownership(self, current_user_id: int, target_user_id: int) -> None:
        """Check if current user owns the resource."""
        if current_user_id != target_user_id:
            raise ForbiddenException()

    async def create(
        self,
        post: PostCreate,
        current_user: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new post.

        Optimized: Uses current_user directly instead of redundant user fetch.

        Parameters
        ----------
        post : PostCreate
            Post creation data
        current_user : dict[str, Any]
            Currently authenticated user (already validated by auth)

        Returns
        -------
        dict[str, Any]
            Created post data

        Raises
        ------
        ModelNotFoundError
            If post creation fails
        """
        post_dict = post.model_dump()
        post_dict["created_by_user_id"] = current_user["id"]

        # Inject tenant_id from context
        tenant = get_tenant_context()
        if tenant is not None:
            post_dict["tenant_id"] = tenant.id

        post_internal = PostCreateInternal(**post_dict)
        created_post = await crud_posts.create(db=self.db, object=post_internal, schema_to_select=PostRead)

        if created_post is None:
            raise ModelNotFoundError("Post", "new")

        return created_post

    async def get_multi(
        self,
        user_id: int,
        page: int = 1,
        items_per_page: int = 10,
    ) -> dict[str, Any]:
        """Get paginated posts for a user.

        Parameters
        ----------
        user_id : int
            User ID to get posts for
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict[str, Any]
            Paginated posts response
        """
        posts_data = await crud_posts.get_multi(
            db=self.db,
            offset=compute_offset(page, items_per_page),
            limit=items_per_page,
            created_by_user_id=user_id,
            is_deleted=False,
        )

        response: dict[str, Any] = paginated_response(crud_data=posts_data, page=page, items_per_page=items_per_page)
        return response

    async def get(self, post_id: int, user_id: int) -> dict[str, Any]:
        """Get a single post by ID.

        Parameters
        ----------
        post_id : int
            Post ID
        user_id : int
            User ID of post author

        Returns
        -------
        dict[str, Any]
            Post data

        Raises
        ------
        ModelNotFoundError
            If post not found
        """
        db_post = await crud_posts.get(
            db=self.db, id=post_id, created_by_user_id=user_id, is_deleted=False, schema_to_select=PostRead
        )
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        return db_post

    async def update(
        self,
        post_id: int,
        values: PostUpdate,
        current_user: dict[str, Any],
    ) -> dict[str, str]:
        """Update a post with ownership validation.

        Parameters
        ----------
        post_id : int
            Post ID
        values : PostUpdate
            Update values
        current_user : dict[str, Any]
            Currently authenticated user

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If post not found
        ForbiddenException
            If current user doesn't own this post
        """
        db_post = await crud_posts.get(db=self.db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        # Ownership check
        if db_post["created_by_user_id"] != current_user["id"]:
            raise ForbiddenException()

        await crud_posts.update(db=self.db, object=values, id=post_id)
        return {"message": "Post updated"}

    async def delete(
        self,
        post_id: int,
        current_user: dict[str, Any],
    ) -> dict[str, str]:
        """Soft delete a post with ownership validation.

        Parameters
        ----------
        post_id : int
            Post ID
        current_user : dict[str, Any]
            Currently authenticated user

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If post not found
        ForbiddenException
            If current user doesn't own this post
        """
        db_post = await crud_posts.get(db=self.db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        # Ownership check
        if db_post["created_by_user_id"] != current_user["id"]:
            raise ForbiddenException()

        await crud_posts.delete(db=self.db, id=post_id)
        return {"message": "Post deleted"}

    async def hard_delete(self, post_id: int) -> dict[str, str]:
        """Permanently delete a post from database (superuser only).

        Parameters
        ----------
        post_id : int
            Post ID

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If post not found
        """
        db_post = await crud_posts.get(db=self.db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        await crud_posts.db_delete(db=self.db, id=post_id)
        return {"message": "Post deleted from the database"}


def get_post_service(db: AsyncSession) -> PostService:
    """Dependency injection factory for PostService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    PostService
        Service instance with database session
    """
    return PostService(db)


# Legacy singleton for backward compatibility with cross-module imports
# TODO: Remove after all cross-module calls are updated to use DI
class _LegacyPostService:
    """Legacy wrapper that accepts db in method calls for backward compatibility."""

    async def create_post(
        self, username: str, post: PostCreate, current_user: dict[str, Any], db: AsyncSession
    ) -> dict[str, Any]:
        service = PostService(db)
        return await service.create(post, current_user)

    async def get_posts(
        self, username: str, db: AsyncSession, page: int = 1, items_per_page: int = 10
    ) -> dict[str, Any]:
        # Need to lookup user_id from username for backward compat
        from ...modules.users.service import UserService

        user_svc = UserService(db)
        user = await user_svc.get_by_username(username)
        service = PostService(db)
        return await service.get_multi(user.id, page, items_per_page)

    async def get_post(self, username: str, post_id: int, db: AsyncSession) -> dict[str, Any]:
        from ...modules.users.service import UserService

        user_svc = UserService(db)
        user = await user_svc.get_by_username(username)
        service = PostService(db)
        return await service.get(post_id, user.id)

    async def update_post(
        self, username: str, post_id: int, values: PostUpdate, current_user: dict[str, Any], db: AsyncSession
    ) -> dict[str, str]:
        service = PostService(db)
        return await service.update(post_id, values, current_user)

    async def delete_post(
        self, username: str, post_id: int, current_user: dict[str, Any], db: AsyncSession
    ) -> dict[str, str]:
        service = PostService(db)
        return await service.delete(post_id, current_user)

    async def hard_delete_post(self, username: str, post_id: int, db: AsyncSession) -> dict[str, str]:
        service = PostService(db)
        return await service.hard_delete(post_id)


post_service = _LegacyPostService()
