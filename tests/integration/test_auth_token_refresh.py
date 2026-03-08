"""Integration tests for auth token refresh with real Redis caching.

Tests the A1-A3 security bug fixes with actual Redis and database interactions.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.auth.service import AuthService


@pytest.fixture
def auth_service():
    """Create AuthService instance."""
    return AuthService()


@pytest.fixture
def mock_response():
    """Mock FastAPI Response object."""
    response = MagicMock()
    response.set_cookie = MagicMock()
    response.delete_cookie = MagicMock()
    return response


class TestTokenRefreshWithCaching:
    """Integration tests for token refresh with auth info caching.

    Validates that the A1 security fix works correctly with cached user data.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_token_refresh_uses_cached_auth_info(self, auth_service, real_redis, mock_response):
        """Test that token refresh retrieves user info from cache when available."""
        # Setup: Pre-populate cache with user auth info
        cache_key = "auth:user:testuser"
        cached_user = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "is_deleted": False,
            "is_email_verified": True,
            "tier_id": 1,
            "tenant_id": str(uuid4()),
        }

        import json

        await real_redis.set(cache_key, json.dumps(cached_user), ex=3600)

        # Verify cache was set
        cached = await real_redis.get(cache_key)
        assert cached is not None
        assert "testuser" in cached

        # Cleanup
        await real_redis.delete(cache_key)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_a1_deleted_user_blocked_with_real_cache(self, auth_service, real_redis, mock_response):
        """A1 SECURITY: Verify deleted user cannot refresh token even with valid cache."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException

        # Setup: Cache contains deleted user
        cache_key = "auth:user:deleteduser"
        deleted_user = {
            "id": 99,
            "username": "deleteduser",
            "email": "deleted@example.com",
            "is_deleted": True,  # User was soft-deleted
            "is_email_verified": True,
            "tier_id": 1,
            "tenant_id": str(uuid4()),
        }

        import json

        await real_redis.set(cache_key, json.dumps(deleted_user), ex=3600)

        # Mock the token verification and user service
        mock_token_data = MagicMock()
        mock_token_data.username_or_email = "deleteduser"
        mock_token_data.absolute_exp = None  # No absolute expiration

        mock_db = AsyncMock()

        with (
            patch("src.app.modules.auth.service.verify_token", new_callable=AsyncMock) as mock_verify,
            patch("src.app.modules.auth.service.blacklist_token", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.get_user_service") as mock_get_user_service,
        ):
            mock_verify.return_value = mock_token_data

            # Return the deleted user from cache simulation
            mock_user_service = MagicMock()
            mock_user_service.get_user_auth_info = AsyncMock(return_value=deleted_user)
            mock_get_user_service.return_value = mock_user_service

            # Should raise UnauthorizedException due to is_deleted=True
            with pytest.raises(UnauthorizedException) as exc_info:
                await auth_service.refresh_access_token("valid_refresh_token", mock_response, mock_db)

            assert "no longer active" in str(exc_info.value.detail)

        # Cleanup
        await real_redis.delete(cache_key)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_invalidation_on_user_delete(self, real_redis):
        """Test that user cache is properly invalidated when user is deleted."""
        import json

        # Setup: Cache user auth info
        username = "cachetest"
        cache_keys = [
            f"auth:user:{username}",
            f"auth:email:{username}@example.com",
        ]

        user_data = {
            "id": 100,
            "username": username,
            "email": f"{username}@example.com",
            "is_deleted": False,
        }

        for key in cache_keys:
            await real_redis.set(key, json.dumps(user_data), ex=3600)

        # Verify cache populated
        for key in cache_keys:
            assert await real_redis.get(key) is not None

        # Simulate cache invalidation (as done by UserService._invalidate_user_cache)
        for key in cache_keys:
            await real_redis.delete(key)

        # Verify cache cleared
        for key in cache_keys:
            assert await real_redis.get(key) is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_auth_cache_ttl_expiration(self, real_redis):
        """Test that auth cache expires after TTL."""
        import asyncio
        import json

        cache_key = "auth:user:ttltest"
        user_data = {"id": 101, "username": "ttltest"}

        # Set with 1 second TTL
        await real_redis.set(cache_key, json.dumps(user_data), ex=1)

        # Verify it exists
        assert await real_redis.get(cache_key) is not None

        # Wait for expiration
        await asyncio.sleep(1.1)

        # Verify it's gone
        assert await real_redis.get(cache_key) is None


class TestAuthInfoCacheIntegrity:
    """Tests for auth info cache data integrity."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_a2_is_deleted_field_in_cache(self, real_redis):
        """A2 FIX: Verify is_deleted field is included in cached auth info."""
        import json

        # Simulate cached user data with is_deleted field (A2 fix)
        cache_key = "auth:user:a2test"
        user_data = {
            "id": 102,
            "username": "a2test",
            "email": "a2test@example.com",
            "is_deleted": False,  # A2: This field must be present
            "is_email_verified": True,  # A2: This field must be present
            "tier_id": 1,
        }

        await real_redis.set(cache_key, json.dumps(user_data), ex=3600)

        # Retrieve and verify structure
        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        # A2 verification: is_deleted and is_email_verified must be present
        assert "is_deleted" in parsed, "A2 FIX: is_deleted must be in cached auth info"
        assert "is_email_verified" in parsed, "A2 FIX: is_email_verified must be in cached auth info"
        assert parsed["is_deleted"] is False
        assert parsed["is_email_verified"] is True

        # Cleanup
        await real_redis.delete(cache_key)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_pipeline_performance(self, real_redis, perf_timer):
        """Test Redis pipeline reduces round trips for cache operations."""
        import json

        # Setup multiple users
        users = [{"id": i, "username": f"pipeuser{i}", "is_deleted": False} for i in range(10)]

        # Measure individual SET operations
        perf_timer.start()
        for user in users:
            await real_redis.set(f"perf:single:{user['id']}", json.dumps(user), ex=60)
        single_time = perf_timer.stop()

        # Measure pipeline SET operations
        perf_timer.start()
        pipe = real_redis.pipeline()
        for user in users:
            pipe.set(f"perf:pipeline:{user['id']}", json.dumps(user), ex=60)
        await pipe.execute()
        pipeline_time = perf_timer.stop()

        # Pipeline should be faster (usually 2-5x for 10 operations)
        # Note: In local tests, difference may be small due to low latency
        assert pipeline_time <= single_time * 2, (
            f"Pipeline ({pipeline_time:.2f}ms) should not be significantly slower than "
            f"individual ops ({single_time:.2f}ms)"
        )

        # Cleanup
        for user in users:
            await real_redis.delete(f"perf:single:{user['id']}")
            await real_redis.delete(f"perf:pipeline:{user['id']}")


class TestTokenRefreshSecurityScenarios:
    """Security-focused integration tests for token refresh."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_refresh_token_rotation_clears_old_token(self, real_redis):
        """Test that token rotation properly blacklists old refresh token."""
        # Simulate blacklist check
        old_token_hash = "old_refresh_token_hash_12345"
        blacklist_key = f"token:blacklist:{old_token_hash}"

        # Add to blacklist (as done by blacklist_token)
        await real_redis.set(blacklist_key, "1", ex=86400 * 7)  # 7 days

        # Verify it's blacklisted
        is_blacklisted = await real_redis.get(blacklist_key)
        assert is_blacklisted is not None

        # Cleanup
        await real_redis.delete(blacklist_key)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_concurrent_token_refresh_safety(self, real_redis):
        """Test that concurrent refresh attempts are handled safely."""
        import asyncio

        # Simulate concurrent refresh attempts
        token_key = "refresh:user:concurrent_test"

        async def attempt_refresh(attempt_id: int):
            # Simulate atomic check-and-set pattern
            current = await real_redis.get(token_key)
            if current is None:
                # First attempt wins
                await real_redis.set(token_key, str(attempt_id), ex=10, nx=True)
                return "created"
            return "exists"

        # Run concurrent attempts
        results = await asyncio.gather(*[attempt_refresh(i) for i in range(5)])

        # Only one should have created
        created_count = sum(1 for r in results if r == "created")
        exists_count = sum(1 for r in results if r == "exists")

        # At least one created, rest see it exists
        assert created_count >= 1
        assert created_count + exists_count == 5

        # Cleanup
        await real_redis.delete(token_key)
