"""Post service layer - encapsulates all post business logic."""

from typing import Any, cast

from fastcrud import compute_offset, paginated_response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.tenant_crud import tenant_scope
from src.app.core.pagination import build_keyset_response, paginate_keyset
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules._base.service import BaseService
from src.app.modules.posts.models import Post
from src.app.modules.posts.schemas import PostCreate, PostCreateInternal, PostRead, PostUpdate
from src.app.shared.exceptions import ForbiddenError, ModelNotFoundError


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

    async def _check_ownership(self, current_user_id: int, target_user_id: Any) -> None:
        """Check if current user owns the resource.

        ``target_user_id`` comes from ORM/dict rows (Any-typed); coerce to
        int so a string/int mismatch can never bypass ownership.
        """
        try:
            target = int(target_user_id)
        except (TypeError, ValueError):
            # Q-M1: raise the shared domain exception, never an HTTP-shaped
            # one — core/errors.py owns the HTTP mapping centrally.
            raise ForbiddenError("You do not have permission to modify this post.") from None
        if current_user_id != target:
            raise ForbiddenError("You do not have permission to modify this post.")

    async def create_post(
        self,
        post: PostCreate,
        current_user: Any,
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
        post_dict["created_by_user_id"] = get_user_attr(current_user, "id")

        post_internal = PostCreateInternal(**post_dict)
        created_post = cast(
            dict[str, Any], await self.crud.create(db=self.db, object=post_internal, schema_to_select=PostRead)
        )

        if created_post is None:
            raise ModelNotFoundError("Post", "new")

        return created_post

    async def get_posts(
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
        # SECURITY FIX: The tenant_id filter is automatically injected by
        # TenantAwareCRUD.get_multi(), preventing cross-tenant leakage.
        filters: dict[str, Any] = {
            "created_by_user_id": user_id,
            "is_deleted": False,
        }

        posts_data = await self.crud.get_multi(
            db=self.db,
            offset=compute_offset(page, items_per_page),
            limit=items_per_page,
            schema_to_select=PostRead,
            **filters,
        )

        response: dict[str, Any] = paginated_response(crud_data=posts_data, page=page, items_per_page=items_per_page)
        return response

    async def get_posts_keyset(
        self,
        user_id: int,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get a keyset-paginated page of a user's posts (P-M2 adoption).

        Constant-cost alternative to ``get_posts`` for deep/infinite-scroll
        listing: OFFSET pages pay O(offset) scan-and-discard (bounded only by
        the 100k ``_MAX_OFFSET`` cap), while this seeks straight to the
        ``(created_at, id)`` cursor position. Mirrors the tenant-isolation
        and ordering conventions of ``audit_logs``' list query: tenant filter
        via ``tenant_scope`` (context wins over caller args), deterministic
        ``created_at DESC, id DESC`` ordering.

        Args:
            user_id: Owner of the posts.
            cursor: Opaque cursor from a previous page's ``next_cursor``
                (None = newest page). Malformed cursors raise ValueError —
                surface as 422.
            limit: Page size.

        Returns:
            Keyset envelope: ``items``, ``next_cursor``, ``has_next``,
            ``limit`` — no ``total`` (counting the remaining set is the exact
            O(N) cost keyset avoids).
        """
        stmt = select(Post).where(
            Post.created_by_user_id == user_id,
            Post.is_deleted.is_(False),
            tenant_scope(Post),  # tenant context injects the isolation filter
        )
        stmt, _decoded = paginate_keyset(stmt, Post.created_at, Post.id, cursor, limit)

        result = await self.db.execute(stmt)
        posts = list(result.scalars().all())

        return build_keyset_response(
            posts,
            limit,
            get_created_at=lambda p: p.created_at,
            get_id=lambda p: p.id,
        )

    async def get_post(self, post_id: int, user_id: int) -> dict[str, Any]:
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
        # SECURITY FIX: The tenant_id filter is automatically injected by
        # TenantAwareCRUD.get(), preventing cross-tenant IDOR.
        filters: dict[str, Any] = {
            "id": post_id,
            "created_by_user_id": user_id,
            "is_deleted": False,
        }

        db_post: dict[str, Any] | None = await self.crud.get(db=self.db, schema_to_select=PostRead, **filters)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        return db_post

    async def update_post(
        self,
        post_id: int,
        values: PostUpdate,
        current_user: Any,
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
        ForbiddenError
            If current user doesn't own this post (Q-M1: shared domain
            exception; HTTP mapping is owned by core/errors.py)
        """
        db_post = await self.crud.get(db=self.db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        # Ownership check
        created_by_user_id = (
            db_post.get("created_by_user_id") if isinstance(db_post, dict) else db_post.created_by_user_id
        )
        await self._check_ownership(get_user_attr(current_user, "id"), created_by_user_id)

        await self.crud.update(db=self.db, object=values, id=post_id)
        return {"message": "Post updated"}

    async def delete_post(
        self,
        post_id: int,
        current_user: Any,
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
        ForbiddenError
            If current user doesn't own this post (Q-M1: shared domain
            exception; HTTP mapping is owned by core/errors.py)
        """
        db_post = await self.crud.get(db=self.db, id=post_id, is_deleted=False, schema_to_select=PostRead)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        # Ownership check
        created_by_user_id = (
            db_post.get("created_by_user_id") if isinstance(db_post, dict) else db_post.created_by_user_id
        )
        await self._check_ownership(get_user_attr(current_user, "id"), created_by_user_id)

        await self.crud.delete(db=self.db, id=post_id)
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
        from src.app.core.tenancy.context import get_current_tenant

        tenant = get_current_tenant()
        db_post = await self.crud.get(db=self.db, id=post_id, is_deleted=False)
        if db_post is None:
            raise ModelNotFoundError("Post", post_id)

        post_tenant = db_post.get("tenant_id") if isinstance(db_post, dict) else getattr(db_post, "tenant_id", None)
        if tenant is not None and post_tenant is not None and str(post_tenant) != str(tenant.id):
            raise ModelNotFoundError("Post", post_id)

        await self.crud.db_delete(db=self.db, id=post_id)
        return {"message": "Post deleted from the database"}


