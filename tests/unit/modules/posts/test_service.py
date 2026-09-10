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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.posts.schemas import PostCreate, PostUpdate
from tests.conftest import MockUser


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
        "tenant_id": MockUser.DEFAULT_TENANT_ID,  # Use UUID
    }


@pytest.fixture
def mock_superuser():
    """Sample superuser data."""
    return {
        "id": 2,
        "username": "admin",
        "email": "admin@example.com",
        "is_superuser": True,
        "tenant_id": MockUser.DEFAULT_TENANT_ID,  # Use UUID
    }


@pytest.fixture
def mock_post():
    """Sample post data."""
    return {
        "id": 1,
        "title": "Test Post",
        "text": "This is a test post content.",
        "media_url": None,
        "created_by_user_id": 1,
        "user_id": 1,
        "tenant_id": MockUser.DEFAULT_TENANT_ID,  # Use UUID
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
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.create.return_value = mock_post

            result = await service.create_post(
                post=post_create,
                current_user=mock_user,
            )

            assert result["id"] == 1
            assert result["title"] == "Test Post"
            mock_crud.create.assert_called_once()


class TestPostServiceGet:
    """Tests for post retrieval."""

    @pytest.mark.asyncio
    async def test_get_post_success(self, mock_db, mock_user, mock_post):
        """Test successful post retrieval by ID."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post

            result = await service.get_post(
                post_id=1,
                user_id=mock_user["id"],
            )

            assert result["id"] == 1
            assert result["title"] == "Test Post"

    @pytest.mark.asyncio
    async def test_get_post_not_found_raises_404(self, mock_db, mock_user):
        """Test that non-existent post raises ModelNotFoundError."""
        from src.app.modules.posts.service import PostService
        from src.app.shared.exceptions import ModelNotFoundError

        service = PostService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = None

            with pytest.raises(ModelNotFoundError):
                await service.get_post(post_id=999, user_id=mock_user["id"])

    @pytest.mark.asyncio
    async def test_get_posts_paginated(self, mock_db, mock_user, mock_post):
        """Test paginated post retrieval."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)
        mock_response = {"data": [mock_post], "total_count": 1}

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get_multi.return_value = mock_response

            result = await service.get_posts(
                user_id=mock_user["id"],
                page=1,
                items_per_page=10,
            )

            assert "data" in result
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_get_posts_keyset_no_cursor(self, mock_db, mock_user):
        """First keyset page: no cursor -> ordered DESC query, full envelope."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)
        rows = [MagicMock(id=3, created_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC))]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = rows
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("src.app.modules.posts.service.tenant_scope", return_value=True),
            patch("src.app.modules.posts.service.select"),
        ):
            result = await service.get_posts_keyset(user_id=mock_user["id"], limit=10)

        assert result["items"] == rows
        # short page => end of listing
        assert result["next_cursor"] is None
        assert result["has_next"] is False
        assert result["limit"] == 10

    @pytest.mark.asyncio
    async def test_get_posts_keyset_full_page_has_next_cursor(self, mock_db, mock_user):
        """A full page yields next_cursor encoding the last row's (created_at, id)."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)
        last_created = datetime(2026, 9, 7, 11, 0, tzinfo=UTC)
        rows = [MagicMock(id=i, created_at=last_created) for i in range(10)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = rows
        mock_db.execute = AsyncMock(return_value=mock_result)

        from src.app.core.pagination import decode_keyset_cursor

        with (
            patch("src.app.modules.posts.service.tenant_scope", return_value=True),
            patch("src.app.modules.posts.service.select"),
        ):
            result = await service.get_posts_keyset(user_id=mock_user["id"], limit=10)

        assert result["has_next"] is True
        created_at, row_id = decode_keyset_cursor(result["next_cursor"])
        assert created_at == last_created
        assert row_id == rows[-1].id

    @pytest.mark.asyncio
    async def test_get_posts_keyset_malformed_cursor_raises(self, mock_db, mock_user):
        """A malformed cursor must raise ValueError before any DB access."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)

        with (
            patch("src.app.modules.posts.service.tenant_scope", return_value=True),
            patch("src.app.modules.posts.service.select"),
        ):
            with pytest.raises(ValueError):
                await service.get_posts_keyset(user_id=mock_user["id"], cursor="!!!bad!!!")

        mock_db.execute.assert_not_called()


class TestPostServiceUpdate:
    """Tests for post updates."""

    @pytest.mark.asyncio
    async def test_update_post_success(self, mock_db, mock_user, mock_post, post_update):
        """Test successful post update."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)
        updated_post = {**mock_post, "title": "Updated Title", "text": "Updated content."}

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post
            mock_crud.update.return_value = updated_post

            result = await service.update_post(
                post_id=1,
                values=post_update,
                current_user=mock_user,
            )

            assert result["message"] == "Post updated"
            mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_post_forbidden_for_other_user(self, mock_db, mock_user, mock_post, post_update):
        """Test that updating another user's post raises the domain ForbiddenError (Q-M1)."""
        from src.app.modules.posts.service import PostService
        from src.app.shared.exceptions import ForbiddenError

        service = PostService(mock_db)
        other_user = {**mock_user, "id": 999, "username": "otheruser"}

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post

            with pytest.raises(ForbiddenError):
                await service.update_post(
                    post_id=1,
                    values=post_update,
                    current_user=other_user,
                )


class TestPostServiceDelete:
    """Tests for post deletion."""

    @pytest.mark.asyncio
    async def test_soft_delete_post_success(self, mock_db, mock_user, mock_post):
        """Test successful soft delete of a post."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post
            mock_crud.delete.return_value = None

            result = await service.delete_post(
                post_id=1,
                current_user=mock_user,
            )

            assert result["message"] == "Post deleted"
            mock_crud.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_delete_post_superuser_only(self, mock_db, mock_user, mock_post):
        """Test hard delete of a post (typically superuser only route)."""
        from src.app.modules.posts.service import PostService

        service = PostService(mock_db)

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post
            mock_crud.db_delete.return_value = None

            result = await service.hard_delete(
                post_id=1,
            )

            assert result["message"] == "Post deleted from the database"
            mock_crud.db_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_post_forbidden_for_other_user(self, mock_db, mock_user, mock_post):
        """Test that deleting another user's post raises the domain ForbiddenError (Q-M1)."""
        from src.app.modules.posts.service import PostService
        from src.app.shared.exceptions import ForbiddenError

        service = PostService(mock_db)
        other_user = {**mock_user, "id": 999, "username": "otheruser"}

        with patch.object(service, "crud", new_callable=AsyncMock) as mock_crud:
            mock_crud.get.return_value = mock_post

            with pytest.raises(ForbiddenError):
                await service.delete_post(
                    post_id=1,
                    current_user=other_user,
                )
