"""Unit tests for PostService business logic.

Tests cover:
- Post creation with ownership validation
- Duplicate handling
- Post retrieval (single and paginated)
- Post updates with ownership checks
- Post soft deletion
- Post hard deletion (superuser)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.app.modules.posts.schemas import PostCreate, PostUpdate


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_user():
    """Sample user data."""
    return {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "is_superuser": False,
        "tenant_id": 1,
    }


@pytest.fixture
def mock_superuser():
    """Sample superuser data."""
    return {
        "id": 2,
        "username": "admin",
        "email": "admin@example.com",
        "is_superuser": True,
        "tenant_id": 1,
    }


@pytest.fixture
def mock_post():
    """Sample post data."""
    return {
        "id": 1,
        "title": "Test Post",
        "text": "This is a test post content.",
        "media_url": None,
        "user_id": 1,
        "tenant_id": 1,
        "created_at": datetime.now(UTC),
        "updated_at": None,
        "is_deleted": False,
        "deleted_at": None,
    }


@pytest.fixture
def post_create():
    """Sample post creation data."""
    return PostCreate(title="Test Post", text="This is a test post content.")


@pytest.fixture
def post_update():
    """Sample post update data."""
    return PostUpdate(title="Updated Title", text="Updated content.")


class TestPostServiceCreate:
    """Tests for post creation."""

    @pytest.mark.asyncio
    async def test_create_post_success(self, mock_db, mock_user, post_create, mock_post):
        """Test successful post creation."""
        from src.app.modules.posts.service import post_service

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
            patch("src.app.modules.posts.service.get_tenant_context") as mock_context,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.create.return_value = mock_post
            mock_tenant = AsyncMock()
            mock_tenant.id = 1
            mock_context.return_value = mock_tenant

            result = await post_service.create_post(
                username="testuser",
                post=post_create,
                current_user=mock_user,
                db=mock_db,
            )

            assert result["id"] == 1
            assert result["title"] == "Test Post"
            mock_crud.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_post_forbidden_for_other_user(self, mock_db, mock_user, post_create):
        """Test that creating post for another user raises ForbiddenException."""
        from src.app.core.exceptions.http_exceptions import ForbiddenException
        from src.app.modules.posts.service import post_service

        other_user = {**mock_user, "id": 999, "username": "otheruser"}

        with patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user:
            mock_get_user.return_value = other_user

            with pytest.raises(ForbiddenException):
                await post_service.create_post(
                    username="otheruser",
                    post=post_create,
                    current_user=mock_user,
                    db=mock_db,
                )


class TestPostServiceGet:
    """Tests for post retrieval."""

    @pytest.mark.asyncio
    async def test_get_post_success(self, mock_db, mock_user, mock_post):
        """Test successful post retrieval by ID."""
        from src.app.modules.posts.service import post_service

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get.return_value = mock_post

            result = await post_service.get_post(
                username="testuser",
                post_id=1,
                db=mock_db,
            )

            assert result["id"] == 1
            assert result["title"] == "Test Post"

    @pytest.mark.asyncio
    async def test_get_post_not_found_raises_404(self, mock_db, mock_user):
        """Test that non-existent post raises ModelNotFoundError."""
        from src.app.modules._base.exceptions import ModelNotFoundError
        from src.app.modules.posts.service import post_service

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get.return_value = None

            with pytest.raises(ModelNotFoundError):
                await post_service.get_post(username="testuser", post_id=999, db=mock_db)

    @pytest.mark.asyncio
    async def test_get_posts_paginated(self, mock_db, mock_user, mock_post):
        """Test paginated post retrieval."""
        from src.app.modules.posts.service import post_service

        mock_response = {"data": [mock_post], "total_count": 1}

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get_multi.return_value = mock_response

            result = await post_service.get_posts(
                username="testuser",
                db=mock_db,
                page=1,
                items_per_page=10,
            )

            assert "data" in result
            assert len(result["data"]) == 1


class TestPostServiceUpdate:
    """Tests for post updates."""

    @pytest.mark.asyncio
    async def test_update_post_success(self, mock_db, mock_user, mock_post, post_update):
        """Test successful post update."""
        from src.app.modules.posts.service import post_service

        updated_post = {**mock_post, "title": "Updated Title", "text": "Updated content."}

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get.return_value = mock_post
            mock_crud.update.return_value = updated_post

            result = await post_service.update_post(
                username="testuser",
                post_id=1,
                values=post_update,
                current_user=mock_user,
                db=mock_db,
            )

            assert result["message"] == "Post updated"
            mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_post_forbidden_for_other_user(self, mock_db, mock_user, mock_post, post_update):
        """Test that updating another user's post raises ForbiddenException."""
        from src.app.core.exceptions.http_exceptions import ForbiddenException
        from src.app.modules.posts.service import post_service

        other_user = {**mock_user, "id": 999, "username": "otheruser"}

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = other_user
            mock_crud.get.return_value = mock_post

            with pytest.raises(ForbiddenException):
                await post_service.update_post(
                    username="otheruser",
                    post_id=1,
                    values=post_update,
                    current_user=mock_user,
                    db=mock_db,
                )


class TestPostServiceDelete:
    """Tests for post deletion."""

    @pytest.mark.asyncio
    async def test_soft_delete_post_success(self, mock_db, mock_user, mock_post):
        """Test successful soft delete of a post."""
        from src.app.modules.posts.service import post_service

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get.return_value = mock_post
            mock_crud.update.return_value = None

            result = await post_service.delete_post(
                username="testuser",
                post_id=1,
                current_user=mock_user,
                db=mock_db,
            )

            assert result["message"] == "Post deleted"
            mock_crud.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_delete_post_superuser_only(self, mock_db, mock_user, mock_post):
        """Test hard delete of a post (typically superuser only route)."""
        from src.app.modules.posts.service import post_service

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = mock_user
            mock_crud.get.return_value = mock_post
            mock_crud.delete.return_value = None

            result = await post_service.hard_delete_post(
                username="testuser",
                post_id=1,
                db=mock_db,
            )

            assert result["message"] == "Post deleted from the database"
            mock_crud.db_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_post_forbidden_for_other_user(self, mock_db, mock_user, mock_post):
        """Test that deleting another user's post raises ForbiddenException."""
        from src.app.core.exceptions.http_exceptions import ForbiddenException
        from src.app.modules.posts.service import post_service

        other_user = {**mock_user, "id": 999, "username": "otheruser"}

        with (
            patch.object(post_service, "_get_user_or_404", new_callable=AsyncMock) as mock_get_user,
            patch("src.app.modules.posts.service.crud_posts", new_callable=AsyncMock) as mock_crud,
        ):
            mock_get_user.return_value = other_user
            mock_crud.get.return_value = mock_post

            with pytest.raises(ForbiddenException):
                await post_service.delete_post(
                    username="otheruser",
                    post_id=1,
                    current_user=mock_user,
                    db=mock_db,
                )
