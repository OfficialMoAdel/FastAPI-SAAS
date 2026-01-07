"""Unit tests for UserService business logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules.users.schemas import UserCreate, UserTierUpdate, UserUpdate
from src.app.modules.users.service import UserService


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def user_service(mock_db):
    """UserService instance."""
    return UserService()


@pytest.fixture
def mock_user_create():
    """Mock UserCreate data."""
    return UserCreate(email="test@example.com", username="testuser", password="SecureP@ssw0rd1", name="Test User")


@pytest.fixture
def mock_user_update():
    """Mock UserUpdate data."""
    return UserUpdate(email="updated@example.com", name="Updated Name")


@pytest.fixture
def mock_user_tier_update():
    """Mock UserTierUpdate data."""
    return UserTierUpdate(tier_id=2)


@pytest.fixture
def mock_db_user():
    """Mock database user dictionary."""
    return {
        "id": 1,
        "email": "test@example.com",
        "username": "testuser",
        "name": "Test User",
        "hashed_password": "hashed_secret",
        "is_active": True,
        "is_superuser": False,
        "tenant_id": 1,
        "tier_id": 1,
    }


class TestUserServiceCreate:
    """Tests for user creation logic."""

    @pytest.mark.asyncio
    async def test_create_user_success(self, user_service, mock_db, mock_user_create, mock_db_user):
        """Test successful user creation."""
        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.users.service.get_password_hash", new_callable=AsyncMock) as mock_hash,
            patch("src.app.modules.users.service.get_tenant_context") as mock_context,
        ):
            # Setup mocks
            mock_crud.exists = AsyncMock(return_value=False)
            mock_hash.return_value = "hashed_password"

            # Mock tenant context
            mock_tenant = MagicMock()
            mock_tenant.id = 1
            mock_context.return_value = mock_tenant

            # Mock create return
            mock_crud.create = AsyncMock(return_value=mock_db_user)

            result = await user_service.create_user(mock_user_create, mock_db)

            # Assertions
            assert result == mock_db_user
            assert mock_crud.exists.call_count == 2  # email and username checks
            mock_hash.assert_called_once_with(password="SecureP@ssw0rd1")
            mock_crud.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email(self, user_service, mock_db, mock_user_create):
        """Test creation fails when email exists."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            # Simulate email exists
            mock_crud.exists = AsyncMock(side_effect=[True, False])  # email exists, username doesn't

            with pytest.raises(DuplicateError) as exc:
                await user_service.create_user(mock_user_create, mock_db)

            assert exc.value.field == "email"

    @pytest.mark.asyncio
    async def test_create_user_duplicate_username(self, user_service, mock_db, mock_user_create):
        """Test creation fails when username exists."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            # Simulate username exists
            mock_crud.exists = AsyncMock(side_effect=[False, True])  # email ok, username exists

            with pytest.raises(DuplicateError) as exc:
                await user_service.create_user(mock_user_create, mock_db)

            assert exc.value.field == "username"

    @pytest.mark.asyncio
    async def test_create_user_creation_failed(self, user_service, mock_db, mock_user_create):
        """Test error raised when creation returns None."""
        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.users.service.get_password_hash", new_callable=AsyncMock) as mock_hash,
            patch("src.app.modules.users.service.get_tenant_context") as mock_context,
        ):
            mock_crud.exists = AsyncMock(return_value=False)
            mock_hash.return_value = "hashed_password"
            mock_context.return_value = MagicMock(id=1)
            mock_crud.create = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await user_service.create_user(mock_user_create, mock_db)


class TestUserServiceGet:
    """Tests for user retrieval."""

    @pytest.mark.asyncio
    async def test_get_user_success(self, user_service, mock_db, mock_db_user):
        """Test getting a user by username."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_db_user)

            result = await user_service.get_user("testuser", mock_db)

            assert result == mock_db_user
            mock_crud.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, user_service, mock_db):
        """Test getting non-existent user raises error."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await user_service.get_user("unknown", mock_db)


class TestUserServiceUpdate:
    """Tests for user updates."""

    @pytest.mark.asyncio
    async def test_update_user_success(self, user_service, mock_db, mock_user_update, mock_db_user):
        """Test successful user update."""
        current_user = {"username": "testuser", "email": "test@example.com"}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_db_user)
            mock_crud.exists = AsyncMock(return_value=False)
            mock_crud.update = AsyncMock()

            result = await user_service.update_user("testuser", mock_user_update, current_user, mock_db)

            assert result["message"] == "User updated"
            mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_user_forbidden(self, user_service, mock_db, mock_user_update, mock_db_user):
        """Test update forbidden for different user."""
        current_user = {"username": "otheruser"}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_db_user)

            with pytest.raises(ForbiddenException):
                await user_service.update_user("testuser", mock_user_update, current_user, mock_db)

    @pytest.mark.asyncio
    async def test_update_user_duplicate_email(self, user_service, mock_db, mock_user_update, mock_db_user):
        """Test update fails if new email is taken."""
        current_user = {"username": "testuser"}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_db_user)
            mock_crud.exists = AsyncMock(return_value=True)  # Email exists

            with pytest.raises(DuplicateError) as exc:
                await user_service.update_user("testuser", mock_user_update, current_user, mock_db)

            assert exc.value.field == "email"


class TestUserServiceDelete:
    """Tests for user deletion."""

    @pytest.mark.asyncio
    async def test_delete_user_success(self, user_service, mock_db, mock_db_user):
        """Test successful user soft deletion."""
        current_user = {"username": "testuser"}

        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.users.service.blacklist_token", new_callable=AsyncMock) as mock_blacklist,
        ):
            mock_crud.get = AsyncMock(return_value=mock_db_user)
            mock_crud.delete = AsyncMock()

            result = await user_service.delete_user("testuser", current_user, "token", mock_db)

            assert result["message"] == "User deleted"
            mock_crud.delete.assert_called_once()
            mock_blacklist.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_user_not_found(self, user_service, mock_db):
        """Test delete fails if user not found."""
        current_user = {"username": "testuser"}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await user_service.delete_user("unknown", current_user, "token", mock_db)

    @pytest.mark.asyncio
    async def test_delete_user_forbidden(self, user_service, mock_db, mock_db_user):
        """Test delete forbidden for different user."""
        current_user = {"username": "otheruser"}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=mock_db_user)

            with pytest.raises(ForbiddenException):
                await user_service.delete_user("testuser", current_user, "token", mock_db)

    @pytest.mark.asyncio
    async def test_hard_delete_user_success(self, user_service, mock_db, mock_db_user):
        """Test successful hard deletion."""
        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.users.service.blacklist_token", new_callable=AsyncMock) as mock_blacklist,
        ):
            mock_crud.exists = AsyncMock(return_value=mock_db_user)
            mock_crud.db_delete = AsyncMock()

            result = await user_service.hard_delete_user("testuser", "token", mock_db)

            assert "deleted from the database" in result["message"]
            mock_crud.db_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_delete_user_not_found(self, user_service, mock_db):
        """Test hard delete fails if user not found."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.exists = AsyncMock(return_value=False)

            with pytest.raises(ModelNotFoundError):
                await user_service.hard_delete_user("unknown", "token", mock_db)


class TestUserServiceTier:
    """Tests for user tier management."""

    @pytest.mark.asyncio
    async def test_update_user_tier_success(self, user_service, mock_db, mock_db_user, mock_user_tier_update):
        """Test successful user tier update."""
        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.tiers.service.tier_service.get_tier_by_id", new_callable=AsyncMock) as mock_get_tier,
        ):
            mock_crud.get = AsyncMock(return_value=mock_db_user)
            mock_crud.update = AsyncMock()
            mock_get_tier.return_value = {"id": 2, "name": "Pro"}

            result = await user_service.update_user_tier("testuser", mock_user_tier_update, mock_db)

            assert "Tier updated" in result["message"]
            mock_crud.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_with_rate_limits(self, user_service, mock_db, mock_db_user):
        """Test getting user with rate limits."""
        with (
            patch("src.app.modules.users.service.crud_users") as mock_crud,
            patch("src.app.modules.tiers.service.tier_service.get_tier_by_id", new_callable=AsyncMock) as mock_get_tier,
            patch(
                "src.app.modules.rate_limits.service.rate_limit_service.get_rate_limits_by_tier_id",
                new_callable=AsyncMock,
            ) as mock_get_limits,
        ):
            mock_crud.get = AsyncMock(return_value=mock_db_user)
            mock_get_tier.return_value = {"id": 1, "name": "Basic"}
            mock_get_limits.return_value = {"data": [{"limit": 100, "name": "api_calls"}]}

            result = await user_service.get_user_with_rate_limits("testuser", mock_db)

            assert result["tier_rate_limits"] == [{"limit": 100, "name": "api_calls"}]

    @pytest.mark.asyncio
    async def test_get_user_with_rate_limits_user_not_found(self, user_service, mock_db):
        """Test get user with rate limits fails if user not found."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await user_service.get_user_with_rate_limits("unknown", mock_db)

    @pytest.mark.asyncio
    async def test_get_user_with_rate_limits_no_tier(self, user_service, mock_db, mock_db_user):
        """Test get user with rate limits when user has no tier."""
        user_no_tier = {**mock_db_user, "tier_id": None}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get = AsyncMock(return_value=user_no_tier)

            result = await user_service.get_user_with_rate_limits("testuser", mock_db)

            assert result["tier_rate_limits"] == []

    @pytest.mark.asyncio
    async def test_get_user_with_tier_eager(self, user_service, mock_db):
        """Test getting user with tier using eager loading."""
        mock_user_with_tier = {
            "id": 1,
            "username": "testuser",
            "tier": {"id": 1, "name": "Basic", "_sa_instance_state": "dummy"},
        }

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get_user_with_tier_eager = AsyncMock(return_value=mock_user_with_tier)

            result = await user_service.get_user_with_tier("testuser", mock_db)

            assert result["tier_name"] == "Basic"
            assert "tier" not in result  # Should be flattened
            assert "_sa_instance_state" not in result.get("tier_name", "")  # Check cleanup

    @pytest.mark.asyncio
    async def test_get_user_with_tier_user_not_found(self, user_service, mock_db):
        """Test get user with tier fails if user not found."""
        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get_user_with_tier_eager = AsyncMock(return_value=None)

            with pytest.raises(ModelNotFoundError):
                await user_service.get_user_with_tier("unknown", mock_db)

    @pytest.mark.asyncio
    async def test_get_user_with_tier_no_tier(self, user_service, mock_db):
        """Test get user with tier returns None if user has no tier."""
        # This case handles line 311-312: if not user_with_tier.get("tier"): return None
        # Mock user exists but has no tier relation loaded/present
        mock_user = {"id": 1, "username": "testuser", "tier": None}

        with patch("src.app.modules.users.service.crud_users") as mock_crud:
            mock_crud.get_user_with_tier_eager = AsyncMock(return_value=mock_user)

            result = await user_service.get_user_with_tier("testuser", mock_db)

            assert result is None
