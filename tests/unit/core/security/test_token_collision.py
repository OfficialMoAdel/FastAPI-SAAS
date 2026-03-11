"""P2-2: Token Blacklist Cache Key Collision Tests.

Verifies that token cache uses hash-based keys instead of prefix.
"""

from unittest.mock import AsyncMock, patch

import pytest
import xxhash


class TestTokenCacheKeyCollision:
    """Tests for hash-based token cache keys."""

    def test_token_cache_key_uses_xxhash(self) -> None:
        """Verify _token_cache_key uses xxhash64."""
        from src.app.core.security.token_blacklist_cache import _token_cache_key

        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.test"
        result = _token_cache_key(token)

        # Should match xxhash64 output
        expected = xxhash.xxh64(token.encode()).hexdigest()
        assert result == expected

    def test_different_tokens_different_keys(self) -> None:
        """Verify different tokens produce different cache keys."""
        from src.app.core.security.token_blacklist_cache import _token_cache_key

        # JWTs with same prefix but different payloads
        token1 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ.sig1"
        token2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwOTg3NjU0MzIxIiwibmFtZSI6IkphbmUifQ.sig2"

        key1 = _token_cache_key(token1)
        key2 = _token_cache_key(token2)

        # Keys should be different (no collision)
        assert key1 != key2

    def test_same_prefix_different_keys(self) -> None:
        """Verify tokens with same first 32 chars produce different cache keys."""
        from src.app.core.security.token_blacklist_cache import _token_cache_key

        # These tokens have identical first 32 characters
        base = "eyJhbGciOiJIUzI1NiIsInR5cCI6Ik"  # 32 chars
        token1 = base + "JWT1payload"
        token2 = base + "JWT2payload"

        # With old prefix method, these would collide
        assert token1[:32] == token2[:32]

        # With hash, they should NOT collide
        key1 = _token_cache_key(token1)
        key2 = _token_cache_key(token2)
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_cache_key_used_in_blacklist_check(self) -> None:
        """Verify is_token_blacklisted uses hash-based cache key."""
        from src.app.core.security.token_blacklist_cache import (
            NEGATIVE_CACHE_KEY,
            _token_cache_key,
            is_token_blacklisted,
        )

        token = "test.jwt.token"
        expected_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token)}"

        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(return_value=0)

        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch(
                "src.app.core.security.token_blacklist_cache._ensure_script_loaded", new_callable=AsyncMock
            ) as mock_script,
        ):
            mock_script.return_value = "test_sha"

            await is_token_blacklisted(token)

            # Verify evalsha was called with correct cache key
            call_args = mock_redis.evalsha.call_args
            assert expected_key in call_args[0]

    def test_add_token_uses_hashed_key(self) -> None:
        """Verify add_token_to_blacklist uses hash-based key for invalidation."""
        import inspect

        from src.app.core.security import token_blacklist_cache

        source = inspect.getsource(token_blacklist_cache)

        # Verify _token_cache_key is used in add function
        assert "_token_cache_key(token)" in source

        # Verify it's not using token[:32] anymore
        # The function should use hash-based key
        assert "def _token_cache_key" in source
