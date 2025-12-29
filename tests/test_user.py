"""Unit tests for user API endpoints."""

from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

from src.app.modules.users.router import erase_user, patch_user, read_user, read_users, write_user
from src.app.core.exceptions.http_exceptions import DuplicateValueException, ForbiddenException, NotFoundException
from src.app.schemas.user import UserCreate, UserRead, UserUpdate


class TestWriteUser:
    """Test user creation endpoint."""

    @pytest.mark.asyncio
    async def test_create_user_success(self, mock_db, sample_user_data, sample_user_read):
        """Test successful user creation."""
        user_create = UserCreate(**sample_user_data)
        expected_result = sample_user_read.model_dump()

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.create_user = AsyncMock(return_value=expected_result)

            result = await write_user(Mock(), user_create, mock_db)

            assert result == expected_result
            mock_service.create_user.assert_called_once_with(user_create, mock_db)

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email(self, mock_db, sample_user_data):
        """Test user creation with duplicate email."""
        user_create = UserCreate(**sample_user_data)

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.create_user = AsyncMock(
                side_effect=DuplicateValueException("Email is already registered")
            )

            with pytest.raises(DuplicateValueException, match="Email is already registered"):
                await write_user(Mock(), user_create, mock_db)

    @pytest.mark.asyncio
    async def test_create_user_duplicate_username(self, mock_db, sample_user_data):
        """Test user creation with duplicate username."""
        user_create = UserCreate(**sample_user_data)

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.create_user = AsyncMock(
                side_effect=DuplicateValueException("Username not available")
            )

            with pytest.raises(DuplicateValueException, match="Username not available"):
                await write_user(Mock(), user_create, mock_db)


class TestReadUser:
    """Test user retrieval endpoint."""

    @pytest.mark.asyncio
    async def test_read_user_success(self, mock_db, sample_user_read):
        """Test successful user retrieval."""
        username = "test_user"
        expected_result = sample_user_read.model_dump()

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.get_user = AsyncMock(return_value=expected_result)

            result = await read_user(Mock(), username, mock_db)

            assert result == expected_result
            mock_service.get_user.assert_called_once_with(username, mock_db)

    @pytest.mark.asyncio
    async def test_read_user_not_found(self, mock_db):
        """Test user retrieval when user doesn't exist."""
        username = "nonexistent_user"

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.get_user = AsyncMock(
                side_effect=NotFoundException("User not found")
            )

            with pytest.raises(NotFoundException, match="User not found"):
                await read_user(Mock(), username, mock_db)


class TestReadUsers:
    """Test users list endpoint."""

    @pytest.mark.asyncio
    async def test_read_users_success(self, mock_db):
        """Test successful users list retrieval."""
        mock_users_data = {"data": [{"id": 1}, {"id": 2}], "total_count": 2}

        with patch("src.app.modules.users.router.crud_users") as mock_crud:
            mock_crud.get_multi = AsyncMock(return_value=mock_users_data)

            with patch("src.app.modules.users.router.paginated_response") as mock_paginated:
                expected_response = {"data": [{"id": 1}, {"id": 2}], "pagination": {}}
                mock_paginated.return_value = expected_response

                result = await read_users(Mock(), mock_db, page=1, items_per_page=10)

                assert result == expected_response
                mock_crud.get_multi.assert_called_once()
                mock_paginated.assert_called_once()


class TestPatchUser:
    """Test user update endpoint."""

    @pytest.mark.asyncio
    async def test_patch_user_success(self, mock_db, current_user_dict, sample_user_read):
        """Test successful user update."""
        username = current_user_dict["username"]
        user_update = UserUpdate(name="New Name")

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.update_user = AsyncMock(return_value={"message": "User updated"})

            result = await patch_user(Mock(), user_update, username, current_user_dict, mock_db)

            assert result == {"message": "User updated"}
            mock_service.update_user.assert_called_once_with(
                username, user_update, current_user_dict, mock_db
            )

    @pytest.mark.asyncio
    async def test_patch_user_forbidden(self, mock_db, current_user_dict, sample_user_read):
        """Test user update when user tries to update another user."""
        username = "different_user"
        user_update = UserUpdate(name="New Name")

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.update_user = AsyncMock(
                side_effect=ForbiddenException("Not authorized to update this user")
            )

            with pytest.raises(ForbiddenException):
                await patch_user(Mock(), user_update, username, current_user_dict, mock_db)


class TestEraseUser:
    """Test user deletion endpoint."""

    @pytest.mark.asyncio
    async def test_erase_user_success(self, mock_db, current_user_dict, sample_user_read):
        """Test successful user deletion."""
        username = current_user_dict["username"]
        token = "mock_token"

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.delete_user = AsyncMock(return_value={"message": "User deleted"})

            result = await erase_user(Mock(), username, current_user_dict, mock_db, token)

            assert result == {"message": "User deleted"}
            mock_service.delete_user.assert_called_once_with(
                username, current_user_dict, token, mock_db
            )

    @pytest.mark.asyncio
    async def test_erase_user_not_found(self, mock_db, current_user_dict):
        """Test user deletion when user doesn't exist."""
        username = "nonexistent_user"
        token = "mock_token"

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.delete_user = AsyncMock(
                side_effect=NotFoundException("User not found")
            )

            with pytest.raises(NotFoundException, match="User not found"):
                await erase_user(Mock(), username, current_user_dict, mock_db, token)

    @pytest.mark.asyncio
    async def test_erase_user_forbidden(self, mock_db, current_user_dict, sample_user_read):
        """Test user deletion when user tries to delete another user."""
        username = "different_user"
        token = "mock_token"

        with patch("src.app.modules.users.router.user_service") as mock_service:
            mock_service.delete_user = AsyncMock(
                side_effect=ForbiddenException("Not authorized to delete this user")
            )

            with pytest.raises(ForbiddenException):
                await erase_user(Mock(), username, current_user_dict, mock_db, token)
