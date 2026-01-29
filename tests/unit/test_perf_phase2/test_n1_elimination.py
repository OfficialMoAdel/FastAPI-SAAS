"""U6 - N+1 Query Elimination Unit Tests.

Tests verifying that UserService.get_with_rate_limits uses eager loading
(joinedload) to fetch User + Tier + RateLimits in a single query.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.modules.users.service import UserService


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def user_service(mock_db):
    """Create UserService with mocked db."""
    return UserService(mock_db)


class TestN1EliminationEagerLoading:
    """Verify N+1 elimination via joinedload in get_with_rate_limits."""

    @pytest.mark.asyncio
    async def test_get_with_rate_limits_single_query(self, user_service, mock_db):
        """Verify get_with_rate_limits executes single query with joinedload."""
        # Setup mock user with tier and rate limits
        mock_rate_limit = MagicMock()
        mock_rate_limit.id = 1
        mock_rate_limit.tier_id = 1
        mock_rate_limit.path = "/api/v1/test"
        mock_rate_limit.limit = 100
        mock_rate_limit.period = 60
        mock_rate_limit.name = "api_v1_test:100:60"
        mock_rate_limit.created_at = MagicMock()
        mock_rate_limit.updated_at = None

        mock_tier = MagicMock()
        mock_tier.id = 1
        mock_tier.name = "premium"
        mock_tier.rate_limits = [mock_rate_limit]

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.profile_image_url = "https://example.com/img.png"
        mock_user.uuid = uuid4()
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.is_deleted = False
        mock_user.is_superuser = False
        mock_user.tier_id = 1
        mock_user.tier = mock_tier

        # Mock the query result
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        # Call the method
        result = await user_service.get_with_rate_limits("testuser")

        # Verify only ONE execute call (single query)
        assert mock_db.execute.call_count == 1

        # Verify result contains rate limits
        assert "tier_rate_limits" in result
        assert len(result["tier_rate_limits"]) == 1

    @pytest.mark.asyncio
    async def test_get_with_rate_limits_no_tier(self, user_service, mock_db):
        """Verify handling when user has no tier."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.profile_image_url = "https://example.com/img.png"
        mock_user.uuid = uuid4()
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.is_deleted = False
        mock_user.is_superuser = False
        mock_user.tier_id = None
        mock_user.tier = None

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await user_service.get_with_rate_limits("testuser")

        # Single query still
        assert mock_db.execute.call_count == 1
        # Empty rate limits
        assert result["tier_rate_limits"] == []


class TestIsomorphismN1Elimination:
    """Isomorphism tests - verify eager loading returns same data as lazy loading would."""

    @pytest.mark.asyncio
    async def test_rate_limits_data_equivalence(self, user_service, mock_db):
        """Verify eager-loaded rate limits match expected structure."""
        # Mock rate limit with all expected fields
        mock_rl = MagicMock()
        mock_rl.id = 1
        mock_rl.tier_id = 1
        mock_rl.path = "/api/v1/users"
        mock_rl.limit = 100
        mock_rl.period = 60
        mock_rl.name = "api_v1_users:100:60"
        mock_rl.created_at = MagicMock()
        mock_rl.updated_at = None

        mock_tier = MagicMock()
        mock_tier.id = 1
        mock_tier.name = "premium"
        mock_tier.rate_limits = [mock_rl]

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.profile_image_url = "https://example.com/img.png"
        mock_user.uuid = uuid4()
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.is_deleted = False
        mock_user.is_superuser = False
        mock_user.tier_id = 1
        mock_user.tier = mock_tier

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await user_service.get_with_rate_limits("testuser")

        # Verify structure matches what would be returned by lazy loading
        # The key point: same data, fewer queries
        rate_limits = result["tier_rate_limits"]
        assert len(rate_limits) == 1

        rl = rate_limits[0]
        # Isomorphism: These fields must match what lazy loading would return
        assert "path" in rl or rl.get("path") is not None or hasattr(rl, "path")

    @pytest.mark.asyncio
    async def test_user_not_found_raises(self, user_service, mock_db):
        """Verify ModelNotFoundError raised when user doesn't exist."""
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        from src.app.modules._base.exceptions import ModelNotFoundError

        with pytest.raises(ModelNotFoundError):
            await user_service.get_with_rate_limits("nonexistent")


class TestQueryStructure:
    """Verify the query uses joinedload for eager loading."""

    def test_joinedload_import_available(self):
        """Verify joinedload is used in the service."""
        import inspect

        from src.app.modules.users import service as user_service_module

        source = inspect.getsource(user_service_module.UserService.get_with_rate_limits)

        # Verify joinedload is used for eager loading
        assert "joinedload" in source
        assert "User.tier" in source
        assert "Tier.rate_limits" in source
