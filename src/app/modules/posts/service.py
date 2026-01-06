"""Post service layer - encapsulates all post business logic."""

from typing import Any

from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ForbiddenException
from ...core.tenancy.context import get_tenant_context
from .._base.exceptions import ModelNotFoundError
from .crud import crud_posts
from .schemas import PostCreate, PostCreateInternal, PostRead, PostUpdate


class PostService:
    """Service class for post-related business logic."""

    async def _get_user_or_404(self, username: str, db: AsyncSession) -> dict[str, Any]:
        """Get user by username or raise NotFoundException.

        Uses UserService for cross-module access instead of direct CRUD import.
        """
        from ...modules.users.service import user_service

        return await user_service.get_user(username, db)

    async def _check_ownership(self, current_user_id: int, target_user_id: int) -> None:
        """Check if current user owns the resource."""
        if current_user_id != target_user_id:
            raise ForbiddenException()

    async def create_post(
        self,
        username: str,
        post: PostCreate,
        current_user: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Create a new post with ownership validation.

        Parameters
        ----------
        username : str
            Username of the post author
        post : PostCreate
            Post creation data
        current_user : dict[str, Any]
            Currently authenticated user
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Created post data

        Raises
        ------
        ModelNotFoundError
            If user not found or post creation fails
        ForbiddenException
            If current user doesn't own this account
        """
        db_user = await self._get_user_or_404(username, db)
        await self._check_ownership(current_user["id"], db_user["id"])

        post_dict = post.model_dump()
        post_dict["created_by_user_id"] = db_user["id"]

        # Inject tenant_id from context
        tenant = get_tenant_context()
        if tenant is not None:
            post_dict["tenant_id"] = tenant.id

        post_internal = PostCreateInternal(**post_dict)
        created_post = await crud_posts.create(db=db, object=post_internal, schema_to_select=PostRead)

        if created_post is None:
            raise ModelNotFoundError("Post", "new")

        return created_post

    async def get_posts(
        self,
        username: str,
        db: AsyncSession,
        page: int = 1,
        items_per_page: int = 10,
    ) -> dict[str, Any]:
        """Get paginated posts for a user.

        Parameters
        ----------
        username : str
            Username to get posts for
        db : AsyncSession
            Database session
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict[str, Any]
            Paginated posts response

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        db_user = await self._get_user_or_404(username, db)

        posts_data = await crud_posts.get_multi(
            db=db,
            offset=compute_offset(page, items_per_page),
            limit=items_per_page,
            created_by_user_id=db_user["id"],
            is_deleted=False,
        )

        response: dict[str, Any] = paginated_response(crud_data=posts_data, page=page, items_per_page=items_per_page)
        return response

    async def get_post(self, username: str, post_id: int, db: AsyncSession) -> dict[str, Any]:
        """Get a single post by ID.

        Parameters
        ----------
        username : str
            Username of post author
        post_id : int
            Post ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Post data

        Raises
        ------
        ModelNotFoundError
            If user or post not found
        """
        db_user = await self._get_user_or_404(username, db)

        db_post = await crud_posts.get(
            db=db, id=post_id, created_by_user_id=db_user["id"], is_deleted=False, schema_to_select=PostRead
        )
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        return db_post

    async def update_post(
        self,
        username: str,
        post_id: int,
        values: PostUpdate,
        current_user: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, str]:
        """Update a post with ownership validation.

        Parameters
        ----------
        username : str
            Username of post author
        post_id : int
            Post ID
        values : PostUpdate
            Update values
        current_user : dict[str, Any]
            Currently authenticated user
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user or post not found
        ForbiddenException
            If current user doesn't own this account
        """
        db_user = await self._get_user_or_404(username, db)
        await self._check_ownership(current_user["id"], db_user["id"])

        db_post = await crud_posts.get(db=db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        await crud_posts.update(db=db, object=values, id=post_id)
        return {"message": "Post updated"}

    async def delete_post(
        self,
        username: str,
        post_id: int,
        current_user: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, str]:
        """Soft delete a post with ownership validation.

        Parameters
        ----------
        username : str
            Username of post author
        post_id : int
            Post ID
        current_user : dict[str, Any]
            Currently authenticated user
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user or post not found
        ForbiddenException
            If current user doesn't own this account
        """
        db_user = await self._get_user_or_404(username, db)
        await self._check_ownership(current_user["id"], db_user["id"])

        db_post = await crud_posts.get(db=db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        await crud_posts.delete(db=db, id=post_id)
        return {"message": "Post deleted"}

    async def hard_delete_post(self, username: str, post_id: int, db: AsyncSession) -> dict[str, str]:
        """Permanently delete a post from database (superuser only).

        Parameters
        ----------
        username : str
            Username of post author
        post_id : int
            Post ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user or post not found
        """
        # Validate user exists (raises ModelNotFoundError if not found)
        await self._get_user_or_404(username, db)

        db_post = await crud_posts.get(db=db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        await crud_posts.db_delete(db=db, id=post_id)
        return {"message": "Post deleted from the database"}


# Singleton instance for import convenience
post_service = PostService()
