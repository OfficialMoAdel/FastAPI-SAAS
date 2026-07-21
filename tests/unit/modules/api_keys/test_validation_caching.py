"""Tests for API key validation caching.

This test suite verifies that:
1. API key validation uses Redis cache effectively
2. Cache hits avoid database queries
3. Cache invalidation works on key revocation
4. Expired keys are handled correctly in cache

Regression test for P1-1: API Key Validation Caching
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.service import APIKeyService


class TestAPIKeyValidationCaching:
    """Test API key validation caching behavior."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> Any:
        """Create APIKeyService with mock database."""
        from src.app.modules.api_keys.service import APIKeyService

        return APIKeyService(mock_db_session)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_data(self, api_key_service: Any) -> None:
        """Cache hit should return cached data without DB query."""
        cached_data = {
            "user_id": 1,
            "tenant_id": 1,
            "scopes": ["read", "write"],
            "key_id": 42,
            "expires_at": None,
        }

        with patch("src.app.modules.api_keys.service.redis_client") as mock_redis:
            cached_json = (
                '{"user_id": 1, "tenant_id": 1, "scopes": ["read", "write"], "key_id": 42, "expires_at": null}'
            )
            mock_redis.get = AsyncMock(return_value=cached_json)

            result = await api_key_service.validate_api_key("test_api_key_12345")

            assert result is not None
            assert result["user_id"] == cached_data["user_id"]
            assert result["tenant_id"] == cached_data["tenant_id"]
            assert result["scopes"] == cached_data["scopes"]
            assert result["key_id"] == cached_data["key_id"]
            # Verify DB was NOT called (cache hit)
            api_key_service.db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db_and_caches_result(self, api_key_service: Any) -> None:
        """Cache miss should query DB and cache the result."""
        # Mock the database response
        mock_key = MagicMock()
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.scopes = '["read"]'
        mock_key.id = 42
        mock_key.expires_at = None
        mock_key.valid_from = None  # Explicitly set to None for comparison

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        api_key_service.db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("src.app.modules.api_keys.service.redis_client") as mock_redis,
            patch("src.app.modules.api_keys.service.crud_api_keys") as mock_crud,
        ):
            mock_redis.get = AsyncMock(return_value=None)  # Cache miss
            mock_redis.setex = AsyncMock()
            mock_crud.update = AsyncMock()

            result = await api_key_service.validate_api_key("test_api_key_12345")

            assert result is not None
            assert result["user_id"] == 1
            # Verify cache was set
            mock_redis.setex.assert_called_once()
            # Verify cache TTL is 300 seconds
            call_args = mock_redis.setex.call_args
            assert call_args[0][1] == 300  # TTL

    @pytest.mark.asyncio
    async def test_expired_key_in_cache_is_deleted(self, api_key_service: Any) -> None:
        """Expired key found in cache should be deleted and return None."""
        expired_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        cached_data = f'{{"user_id": 1, "tenant_id": 1, "scopes": [], "key_id": 42, "expires_at": "{expired_time}"}}'

        with patch("src.app.modules.api_keys.service.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=cached_data)
            mock_redis.delete = AsyncMock()

            result = await api_key_service.validate_api_key("test_api_key_12345")

            assert result is None
            # Verify expired key was deleted from cache
            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_expired_key_in_cache_is_returned(self, api_key_service: Any) -> None:
        """Non-expired key in cache should be returned."""
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        cached_data = (
            f'{{"user_id": 1, "tenant_id": 1, "scopes": ["admin"], "key_id": 42, "expires_at": "{future_time}"}}'
        )

        with patch("src.app.modules.api_keys.service.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=cached_data)

            result = await api_key_service.validate_api_key("test_api_key_12345")

            assert result is not None
            assert result["scopes"] == ["admin"]

    @pytest.mark.asyncio
    async def test_cache_key_format(self, api_key_service: Any) -> None:
        """Cache key should use full hash to prevent collisions."""
        test_hash = "abcdef1234567890deadbeef"
        cache_key = api_key_service._get_cache_key(test_hash)

        assert cache_key == f"api_key:{test_hash}"
        assert cache_key.split(":")[1] == test_hash


class TestAPIKeyRevocationCacheInvalidation:
    """Test cache invalidation on API key revocation."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> Any:
        """Create APIKeyService with mock database."""
        from src.app.modules.api_keys.service import APIKeyService

        return APIKeyService(mock_db_session)

    @pytest.mark.asyncio
    async def test_revoke_invalidates_cache(self, api_key_service: Any) -> None:
        """Revoking an API key should invalidate its cache entry."""
        from src.app.modules.api_keys.models import APIKey

        # Create a proper mock APIKey model object
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 42
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.hashed_key = "abcdef1234567890deadbeefcafe1234"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        api_key_service.db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("src.app.modules.api_keys.service.redis_client") as mock_redis,
            patch("src.app.modules.api_keys.service.crud_api_keys") as mock_crud,
        ):
            mock_crud.update = AsyncMock()
            mock_redis.delete = AsyncMock()

            await api_key_service.revoke_api_key(key_id=42, user_id=1, tenant_id=1)

            # Verify cache was invalidated
            mock_redis.delete.assert_called_once()
            call_args = mock_redis.delete.call_args[0][0]
            assert call_args.startswith("api_key:")

    @pytest.mark.asyncio
    async def test_revoke_without_redis_still_works(self, api_key_service: Any) -> None:
        """Revocation should work even if Redis is unavailable."""
        from src.app.modules.api_keys.models import APIKey

        # Create a proper mock APIKey model object
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 42
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.hashed_key = "abcdef1234567890deadbeefcafe1234"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        api_key_service.db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("src.app.modules.api_keys.service.redis_client", None),
            patch("src.app.modules.api_keys.service.crud_api_keys") as mock_crud,
        ):
            mock_crud.update = AsyncMock()

            result = await api_key_service.revoke_api_key(key_id=42, user_id=1, tenant_id=1)

            assert result["message"] == "API key revoked successfully"
            mock_crud.update.assert_called_once()


class TestAPIKeyValidationWithoutRedis:
    """Test API key validation when Redis is unavailable."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> Any:
        """Create APIKeyService with mock database."""
        from src.app.modules.api_keys.service import APIKeyService

        return APIKeyService(mock_db_session)

    @pytest.mark.asyncio
    async def test_validation_works_without_redis(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that validation works even if Redis is unavailable."""
        # Ensure redis is mocked
        api_key_service.redis = MagicMock()

        # Mock Redis to raise connection error
        api_key_service.redis.get.side_effect = ConnectionError("Redis down")
        api_key_service.redis.setex.side_effect = ConnectionError("Redis down")

        # Mock DB result
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.expires_at = None
        mock_key.scopes = None
        mock_key.valid_from = None  # Explicitly set to None for comparison

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        api_key_service.db.execute = AsyncMock(return_value=mock_result)

        # Mock crud update for last_used_at
        with patch("src.app.modules.api_keys.service.crud_api_keys.update") as mock_update:
            mock_update.return_value = None

            result = await api_key_service.validate_api_key("hashed_key_123")

            assert result is not None
            assert result["key_id"] == 1
            # Should have tried DB
            api_key_service.db.execute.assert_called()
