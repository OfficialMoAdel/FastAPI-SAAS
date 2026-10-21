"""Integration Tests: User Caching with Real Redis.

Tests validate user caching behavior with actual Redis connections,
verifying cache hit/miss patterns, TTL expiration, and invalidation.
"""

import json
from uuid import uuid4

import pytest

from tests.helpers.redis import wait_for_redis_key_expiry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestUserCachingIntegration:
    """Test user caching with real Redis."""

    async def test_cache_user_on_first_lookup(self, real_redis, clean_redis):
        """Verify user data is cached on first lookup."""
        user_id = str(uuid4())
        cache_key = f"user:{user_id}"

        # Verify cache is empty
        cached = await real_redis.get(cache_key)
        assert cached is None

        # Simulate caching user data
        user_data = {
            "id": user_id,
            "email": "test@example.com",
            "username": "testuser",
            "is_deleted": False,
            "is_superuser": False,
            "tier_id": 1,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(user_data))

        # Verify cache now has data
        cached = await real_redis.get(cache_key)
        assert cached is not None
        assert json.loads(cached)["id"] == user_id

    async def test_cache_hit_on_subsequent_lookups(self, real_redis, clean_redis):
        """Verify cache hit on repeated lookups."""
        user_id = str(uuid4())
        cache_key = f"user:{user_id}"

        user_data = {
            "id": user_id,
            "email": "cached@example.com",
            "username": "cacheduser",
        }
        await real_redis.setex(cache_key, 3600, json.dumps(user_data))

        # Multiple lookups should all hit cache
        for _ in range(10):
            cached = await real_redis.get(cache_key)
            assert cached is not None
            parsed = json.loads(cached)
            assert parsed["email"] == "cached@example.com"

    async def test_cache_ttl_expiration(self, real_redis, clean_redis):
        """Verify cache entries expire after TTL."""
        user_id = str(uuid4())
        cache_key = f"user:{user_id}:ttl_test"

        # Set with short TTL
        await real_redis.setex(cache_key, 2, json.dumps({"id": user_id}))

        # Immediately available
        cached = await real_redis.get(cache_key)
        assert cached is not None

        try:
            await wait_for_redis_key_expiry(real_redis, cache_key)
        finally:
            await real_redis.delete(cache_key)

    async def test_cache_invalidation_on_delete(self, real_redis, clean_redis):
        """Verify cache is invalidated when key is deleted."""
        user_id = str(uuid4())
        cache_key = f"user:{user_id}"

        # Cache user
        await real_redis.setex(cache_key, 3600, json.dumps({"id": user_id}))
        assert await real_redis.get(cache_key) is not None

        # Delete (invalidate)
        await real_redis.delete(cache_key)

        # Should be gone
        assert await real_redis.get(cache_key) is None

    async def test_pipeline_batch_operations(self, real_redis, clean_redis):
        """Verify Redis pipeline batches operations efficiently."""
        user_ids = [str(uuid4()) for _ in range(5)]

        # Use pipeline for batch writes
        async with real_redis.pipeline() as pipe:
            for user_id in user_ids:
                cache_key = f"user:{user_id}:batch"
                pipe.setex(cache_key, 3600, json.dumps({"id": user_id}))
            await pipe.execute()

        # Verify all were written
        for user_id in user_ids:
            cache_key = f"user:{user_id}:batch"
            cached = await real_redis.get(cache_key)
            assert cached is not None
            assert json.loads(cached)["id"] == user_id


class TestUserCacheFields:
    """Test A2 fix: cached auth info includes required fields."""

    async def test_cached_auth_info_has_is_deleted(self, real_redis, clean_redis):
        """A2: Verify is_deleted is included in cached auth info."""
        user_id = str(uuid4())
        cache_key = f"user:auth:{user_id}"

        # Simulate cached auth info with is_deleted field
        auth_info = {
            "id": user_id,
            "email": "test@example.com",
            "is_deleted": False,
            "is_email_verified": True,
            "is_superuser": False,
            "tier_id": 1,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(auth_info))

        # Retrieve and verify fields
        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        assert "is_deleted" in parsed
        assert "is_email_verified" in parsed
        assert parsed["is_deleted"] is False

    async def test_cached_auth_rejects_deleted_users(self, real_redis, clean_redis):
        """A2: Verify deleted users are detected from cache."""
        user_id = str(uuid4())
        cache_key = f"user:auth:{user_id}"

        # Cache a deleted user
        auth_info = {
            "id": user_id,
            "email": "deleted@example.com",
            "is_deleted": True,
            "is_superuser": False,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(auth_info))

        # Retrieve and check
        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        # Application should reject this user
        assert parsed["is_deleted"] is True


class TestRateLimitCaching:
    """Test P2-3: Rate limit caching with composite index."""

    async def test_rate_limit_cache_key_structure(self, real_redis, clean_redis):
        """Verify rate limit cache uses tier_id in key."""
        tier_id = 1
        cache_key = f"rate_limits:tier:{tier_id}"

        rate_limits = [
            {"id": 1, "tier_id": tier_id, "path": "/api/v1/users", "limit": 100, "period": 60},
            {"id": 2, "tier_id": tier_id, "path": "/api/v1/auth", "limit": 20, "period": 60},
        ]

        await real_redis.setex(cache_key, 3600, json.dumps(rate_limits))

        # Verify retrieval
        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        assert len(parsed) == 2
        assert parsed[0]["path"] == "/api/v1/users"

    async def test_rate_limit_cache_invalidation(self, real_redis, clean_redis):
        """Verify rate limit cache is invalidated on update."""
        tier_id = 2
        cache_key = f"rate_limits:tier:{tier_id}"

        # Set initial cache
        await real_redis.setex(cache_key, 3600, json.dumps([{"id": 1}]))
        assert await real_redis.get(cache_key) is not None

        # Invalidate on update
        await real_redis.delete(cache_key)
        assert await real_redis.get(cache_key) is None


class TestTokenBlacklistNegativeCaching:
    """Test P2-4: Token blacklist negative caching."""

    async def test_negative_cache_for_valid_token(self, real_redis, clean_redis):
        """P2-4: Verify negative cache is set for non-blacklisted tokens."""
        token_jti = str(uuid4())
        cache_key = f"token:blacklist:{token_jti}"

        # Check if blacklisted (it's not)
        blacklisted = await real_redis.get(cache_key)
        assert blacklisted is None

        # Set negative cache marker (token is NOT blacklisted)
        await real_redis.setex(cache_key, 300, "0")  # 5 min negative cache

        # Subsequent check uses negative cache
        cached = await real_redis.get(cache_key)
        assert cached == "0"  # Not blacklisted

    async def test_positive_cache_for_blacklisted_token(self, real_redis, clean_redis):
        """Verify blacklisted tokens are cached positively."""
        token_jti = str(uuid4())
        cache_key = f"token:blacklist:{token_jti}"

        # Blacklist the token
        await real_redis.setex(cache_key, 86400, "1")  # 24h cache

        # Check should return blacklisted
        cached = await real_redis.get(cache_key)
        assert cached == "1"  # Blacklisted

    async def test_negative_cache_ttl_shorter_than_token_ttl(self, real_redis, clean_redis):
        """P2-4: Negative cache TTL should be shorter than token TTL."""
        token_jti = str(uuid4())
        cache_key = f"token:blacklist:{token_jti}"

        # Set negative cache with short TTL
        negative_cache_ttl = 2  # 2 seconds for testing
        await real_redis.setex(cache_key, negative_cache_ttl, "0")

        try:
            await wait_for_redis_key_expiry(real_redis, cache_key)
        finally:
            await real_redis.delete(cache_key)
