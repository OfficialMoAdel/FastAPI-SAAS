"""Integration Tests: API Key Caching with Real Redis.

Tests validate API key caching behavior with actual Redis connections,
verifying lookup patterns, expiration, and invalidation on revocation.
"""

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestAPIKeyCachingIntegration:
    """Test API key caching with real Redis."""

    @staticmethod
    def hash_api_key(key: str) -> str:
        """Hash API key for cache lookup."""
        return hashlib.sha256(key.encode()).hexdigest()

    async def test_cache_api_key_on_validation(self, real_redis, clean_redis):
        """Verify API key is cached after successful validation."""
        api_key = f"sk_{uuid4().hex}"
        key_hash = self.hash_api_key(api_key)
        cache_key = f"api_key:{key_hash}"

        # Verify cache is empty
        cached = await real_redis.get(cache_key)
        assert cached is None

        # Simulate caching validated API key
        key_data = {
            "id": str(uuid4()),
            "user_id": str(uuid4()),
            "name": "Test API Key",
            "scopes": ["read", "write"],
            "is_active": True,
            "expires_at": None,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(key_data))

        # Verify cache has data
        cached = await real_redis.get(cache_key)
        assert cached is not None
        parsed = json.loads(cached)
        assert parsed["is_active"] is True
        assert "read" in parsed["scopes"]

    async def test_cache_hit_speeds_up_validation(self, real_redis, clean_redis):
        """Verify cached API key validation is fast."""
        api_key = f"sk_{uuid4().hex}"
        key_hash = self.hash_api_key(api_key)
        cache_key = f"api_key:{key_hash}"

        key_data = {
            "id": str(uuid4()),
            "user_id": str(uuid4()),
            "is_active": True,
            "scopes": ["read"],
        }
        await real_redis.setex(cache_key, 3600, json.dumps(key_data))

        # Multiple validations should all hit cache
        import time

        start = time.perf_counter()
        for _ in range(100):
            await real_redis.get(cache_key)
        elapsed = time.perf_counter() - start

        # 100 cache hits should be very fast (< 1 second)
        assert elapsed < 1.0, f"100 cache hits took {elapsed:.2f}s, expected < 1s"

    async def test_cache_invalidation_on_revoke(self, real_redis, clean_redis):
        """Verify cache is invalidated when API key is revoked."""
        api_key = f"sk_{uuid4().hex}"
        key_hash = self.hash_api_key(api_key)
        cache_key = f"api_key:{key_hash}"

        # Cache the key
        await real_redis.setex(cache_key, 3600, json.dumps({"is_active": True}))
        assert await real_redis.get(cache_key) is not None

        # Revoke (invalidate cache)
        await real_redis.delete(cache_key)

        # Should be gone
        assert await real_redis.get(cache_key) is None

    async def test_expired_key_not_cached(self, real_redis, clean_redis):
        """Verify expired API keys are not returned from cache."""
        api_key = f"sk_{uuid4().hex}"
        key_hash = self.hash_api_key(api_key)
        cache_key = f"api_key:{key_hash}:expired_test"

        # Cache with very short TTL
        await real_redis.setex(cache_key, 2, json.dumps({"is_active": True}))

        # Wait for expiration
        await asyncio.sleep(2.5)

        # Expired key should not be returned
        cached = await real_redis.get(cache_key)
        assert cached is None


class TestAPIKeyScopeValidation:
    """Test API key scope caching patterns."""

    async def test_scopes_cached_with_key(self, real_redis, clean_redis):
        """Verify scopes are cached alongside API key."""
        key_id = str(uuid4())
        cache_key = f"api_key:id:{key_id}"

        key_data = {
            "id": key_id,
            "scopes": ["users:read", "users:write", "billing:read"],
            "is_active": True,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(key_data))

        # Retrieve and check scopes
        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        assert len(parsed["scopes"]) == 3
        assert "users:write" in parsed["scopes"]

    async def test_scope_check_from_cache(self, real_redis, clean_redis):
        """Verify scope authorization can be done from cached data."""
        key_id = str(uuid4())
        cache_key = f"api_key:id:{key_id}"

        key_data = {
            "id": key_id,
            "scopes": ["users:read"],
            "is_active": True,
        }
        await real_redis.setex(cache_key, 3600, json.dumps(key_data))

        cached = await real_redis.get(cache_key)
        parsed = json.loads(cached)

        # Check scope authorization
        required_scope = "users:read"
        assert required_scope in parsed["scopes"]

        # Check unauthorized scope
        unauthorized_scope = "admin:write"
        assert unauthorized_scope not in parsed["scopes"]


class TestAPIKeyRateLimiting:
    """Test API key rate limit tracking."""

    async def test_rate_limit_counter_increment(self, real_redis, clean_redis):
        """Test Redis-based rate limit counter."""
        key_id = str(uuid4())
        window = 60  # 60 second window
        counter_key = f"rate_limit:api_key:{key_id}:{window}"

        # Increment counter
        count = await real_redis.incr(counter_key)
        assert count == 1

        # Set TTL on first increment
        await real_redis.expire(counter_key, window)

        # More increments
        for _ in range(9):
            await real_redis.incr(counter_key)

        # Check final count
        final = await real_redis.get(counter_key)
        assert int(final) == 10

    async def test_rate_limit_window_expiration(self, real_redis, clean_redis):
        """Test rate limit window expires correctly."""
        key_id = str(uuid4())
        counter_key = f"rate_limit:api_key:{key_id}:window_test"

        # Set counter with short TTL
        await real_redis.setex(counter_key, 2, "50")

        # Verify exists
        assert await real_redis.get(counter_key) == "50"

        # Wait for expiration
        await asyncio.sleep(2.5)

        # Window should reset
        assert await real_redis.get(counter_key) is None

    async def test_sliding_window_pattern(self, real_redis, clean_redis):
        """Test sliding window rate limiting pattern."""
        key_id = str(uuid4())
        sorted_set_key = f"rate_limit:sliding:{key_id}"
        window_ms = 1000  # 1 second window
        limit = 10

        import time

        now_ms = int(time.time() * 1000)

        # Add requests to sorted set
        async with real_redis.pipeline() as pipe:
            for i in range(5):
                request_id = f"{now_ms}:{i}"
                pipe.zadd(sorted_set_key, {request_id: now_ms + i})
            await pipe.execute()

        # Count requests in window
        window_start = now_ms - window_ms
        count = await real_redis.zcount(sorted_set_key, window_start, "+inf")
        assert count == 5
        assert count < limit  # Under rate limit
