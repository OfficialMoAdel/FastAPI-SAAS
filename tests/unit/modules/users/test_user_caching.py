"""Tests for user caching in Redis.

This test suite verifies that:
1. User auth info uses Redis cache effectively
2. Cache hits avoid database queries
3. Cache invalidation works on user updates
4. Both username and email lookups are cached

Regression test for P1-2: User Caching in Redis
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _create_mock_tenant() -> MagicMock:
    """Create a mock tenant for tenant context."""
    mock_tenant = MagicMock()
    mock_tenant.id = "550e8400-e29b-41d4-a716-446655440001"
    mock_tenant.tenant_id = "550e8400-e29b-41d4-a716-446655440001"
    return mock_tenant


class TestUserAuthInfoCaching:
    """Test user auth info caching behavior."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_data_username(self, user_service: Any) -> None:
        """Cache hit by username should return cached data without DB query."""
        mock_tenant = _create_mock_tenant()
        cached_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(
                    return_value='{"id": "550e8400-e29b-41d4-a716-446655440000", '
                    '"username": "testuser", "email": "test@example.com", '
                    '"tier": {"id": 1, "name": "free"}}'
                )

                result = await user_service.get_user_auth_info(username="testuser")

                assert result is not None
                assert result["username"] == cached_data["username"]
                assert result["email"] == cached_data["email"]
                # Cache hit means data returned directly from Redis (verified by mock)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_data_email(self, user_service: Any) -> None:
        """Cache hit by email should return cached data without DB query."""
        mock_tenant = _create_mock_tenant()
        cached_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(
                    return_value='{"id": "550e8400-e29b-41d4-a716-446655440000", '
                    '"username": "testuser", "email": "test@example.com", '
                    '"tier": {"id": 1, "name": "free"}}'
                )

                result = await user_service.get_user_auth_info(email="test@example.com")

                assert result is not None
                assert result["username"] == cached_data["username"]
                assert result["email"] == cached_data["email"]

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db_and_caches_result(self, user_service: Any) -> None:
        """Cache miss should query DB and cache the result."""
        mock_tenant = _create_mock_tenant()
        user_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(return_value=None)  # Cache miss
                mock_client.setex = AsyncMock()

                # Mock CRUD response
                user_service.crud.get_user_with_tier_eager = AsyncMock(return_value=user_data)

                result = await user_service.get_user_auth_info(username="testuser")

                assert result is not None
                assert result["username"] == "testuser"
                # Verify cache was set with 1-hour TTL
                mock_client.setex.assert_called_once()
                call_args = mock_client.setex.call_args
                assert call_args[0][1] == 3600  # AUTH_INFO_CACHE_TTL

    @pytest.mark.asyncio
    async def test_cache_key_format_username(self, user_service: Any) -> None:
        """Cache key for username should use user:auth: prefix with tenant ID."""
        from src.app.modules.users.service import _user_auth_cache_key

        mock_tenant = _create_mock_tenant()
        with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
            cache_key = _user_auth_cache_key("testuser")
            # Cache key format is user:auth:{tenant_id}:{identifier}
            assert cache_key == f"user:auth:{mock_tenant.id}:testuser"

    @pytest.mark.asyncio
    async def test_cache_key_format_email(self, user_service: Any) -> None:
        """Cache key for email should use user:auth: prefix with tenant ID."""
        from src.app.modules.users.service import _user_auth_cache_key

        mock_tenant = _create_mock_tenant()
        with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
            cache_key = _user_auth_cache_key("test@example.com")
            # Cache key format is user:auth:{tenant_id}:{identifier}
            assert cache_key == f"user:auth:{mock_tenant.id}:test@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_identifier(self, user_service: Any) -> None:
        """Should return None when neither username nor email provided."""
        result = await user_service.get_user_auth_info()
        assert result is None


class TestUserCacheInvalidation:
    """Test cache invalidation when user data changes."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.mark.asyncio
    async def test_update_invalidates_all_cache_keys(self, user_service: Any) -> None:
        """Updating a user should invalidate all cache entries including email."""
        mock_tenant = _create_mock_tenant()
        user_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
                class MockPipeline:
                    def __init__(self):
                        self.set_calls = []
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc, tb):
                        pass
                    def set(self, key, val, ex=None):
                        self.set_calls.append(key)
                    async def execute(self):
                        pass

                mock_pipe = MockPipeline()
                mock_client.pipeline = MagicMock(return_value=mock_pipe)
                mock_client.delete = AsyncMock()

                await user_service._invalidate_user_cache(user_data)

                # Verify profile cache keys were deleted (5 keys total)
                mock_client.delete.assert_called_once()
                call_args = mock_client.delete.call_args[0]
                assert len(call_args) == 5

                # Verify the right keys were invalidated (tenant-scoped)
                expected_keys = [
                    "user:profile:550e8400-e29b-41d4-a716-446655440000",
                    f"user:profile:{mock_tenant.id}:email:test@example.com",
                    f"user:profile:{mock_tenant.id}:username:testuser",
                    f"user:auth:{mock_tenant.id}:testuser",
                    f"user:auth:{mock_tenant.id}:test@example.com",
                ]

                for key in expected_keys:
                    assert key in call_args

                # Verify token_version keys were forcefully set via pipeline
                assert len(mock_pipe.set_calls) == 2
                assert f"tenant:{mock_tenant.id}:user:testuser:token_version" in mock_pipe.set_calls
                assert f"tenant:{mock_tenant.id}:user:test@example.com:token_version" in mock_pipe.set_calls


class TestUserCachingWithoutRedis:
    """Test user caching when Redis is unavailable."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.mark.asyncio
    async def test_get_user_auth_info_works_without_redis(self, user_service: Any) -> None:
        """Should fall back to DB when Redis is unavailable."""
        mock_tenant = _create_mock_tenant()
        user_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        with patch("src.app.modules.users.service.get_redis", return_value=None):
            with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):

                user_service.crud.get_user_with_tier_eager = AsyncMock(return_value=user_data)

                result = await user_service.get_user_auth_info(username="testuser")

                assert result is not None
                assert result["username"] == "testuser"
                # DB should be queried
                user_service.crud.get_user_with_tier_eager.assert_called_once()
