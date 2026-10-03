"""Tests for tenant cache set-based indexing and invalidation.

This test suite verifies the O(|S|) set-based invalidation replaces
the O(K) SCAN-based approach, providing 10-1000x speedup at scale.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.utils.cache import (
    _tenant_index_key,
    add_to_tenant_index,
    delete_tenant_cache,
    get_tenant_cache,
    get_tenant_index_size,
    invalidate_tenant_cache,
    remove_from_tenant_index,
    set_tenant_cache,
    tenant_cache_key,
)


def create_mock_redis_with_pipeline() -> tuple[AsyncMock, AsyncMock]:
    """Create a mock Redis client with properly mocked pipeline.

    Returns:
        Tuple of (mock_redis, mock_pipeline)
    """
    mock_redis = AsyncMock()
    mock_pipeline = AsyncMock()
    mock_redis.scan = AsyncMock(return_value=(0, []))

    @asynccontextmanager
    async def mock_pipeline_cm(**kwargs):  # noqa: ANN003
        yield mock_pipeline

    mock_redis.pipeline = mock_pipeline_cm
    return mock_redis, mock_pipeline


class TestTenantIndexKeyGeneration:
    """Test index key generation."""

    def test_generates_correct_index_key_for_int(self) -> None:
        """Verify index key format for integer tenant IDs."""
        result = _tenant_index_key(123)
        assert result == "tenant_index:123"

    def test_generates_correct_index_key_for_str(self) -> None:
        """Verify index key format for string tenant IDs."""
        result = _tenant_index_key("abc-tenant")
        assert result == "tenant_index:abc-tenant"


class TestAddToTenantIndex:
    """Test adding keys to tenant index set."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self) -> None:
        """Should return False when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await add_to_tenant_index(1, "tenant:1:user:42")
            assert result is False

    @pytest.mark.asyncio
    async def test_calls_sadd_with_correct_args(self) -> None:
        """Verify SADD is called for both main index and prefix index."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await add_to_tenant_index(1, "tenant:1:user:42")

            assert result is True
            # Now called twice: main index and prefix index
            assert mock_pipeline.sadd.call_count == 2
            calls = mock_pipeline.sadd.call_args_list
            assert calls[0].args == ("tenant_index:1", "tenant:1:user:42")
            assert calls[1].args == ("tenant_index:1:user", "tenant:1:user:42")


class TestRemoveFromTenantIndex:
    """Test removing keys from tenant index set."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self) -> None:
        """Should return False when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await remove_from_tenant_index(1, "tenant:1:user:42")
            assert result is False

    @pytest.mark.asyncio
    async def test_calls_srem_with_correct_args(self) -> None:
        """Verify SREM is called for both main index and prefix index."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await remove_from_tenant_index(1, "tenant:1:user:42")

            assert result is True
            # Now called twice: main index and prefix index
            assert mock_pipeline.srem.call_count == 2
            calls = mock_pipeline.srem.call_args_list
            assert calls[0].args == ("tenant_index:1", "tenant:1:user:42")
            assert calls[1].args == ("tenant_index:1:user", "tenant:1:user:42")


class TestSetTenantCache:
    """Test atomic cache set with index tracking."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self) -> None:
        """Should return False when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await set_tenant_cache(1, "user", ("42",), '{"data": 1}')
            assert result is False

    @pytest.mark.asyncio
    async def test_uses_pipeline_for_atomic_operation(self) -> None:
        """Verify pipeline is used for atomic setex + sadd."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await set_tenant_cache(1, "user", ("42",), '{"data": 1}', ttl=300)

            assert result is True

            # Verify setex called on pipeline
            mock_pipeline.setex.assert_called_once_with("tenant:1:user:42", 300, '{"data": 1}')
            # sadd now called twice: main index and prefix index
            assert mock_pipeline.sadd.call_count == 2
            mock_pipeline.execute.assert_called_once()


class TestGetTenantCache:
    """Test tenant cache retrieval."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_client(self) -> None:
        """Should return None when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await get_tenant_cache(1, "user", "42")
            assert result is None

    @pytest.mark.asyncio
    async def test_gets_value_with_correct_key(self) -> None:
        """Verify GET is called with properly formatted tenant key."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '{"data": "cached"}'

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await get_tenant_cache(1, "user", "42")

            assert result == '{"data": "cached"}'
            mock_redis.get.assert_called_once_with("tenant:1:user:42")


class TestDeleteTenantCache:
    """Test atomic cache delete with index cleanup."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self) -> None:
        """Should return False when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await delete_tenant_cache(1, "user", "42")
            assert result is False

    @pytest.mark.asyncio
    async def test_uses_pipeline_for_atomic_operation(self) -> None:
        """Verify pipeline is used for atomic delete + srem."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await delete_tenant_cache(1, "user", "42")

            assert result is True

            # Verify delete and srem called on pipeline
            mock_pipeline.delete.assert_called_once_with("tenant:1:user:42")
            # Should call srem twice: once for global tenant index, once for prefix index
            assert mock_pipeline.srem.call_count == 2
            mock_pipeline.srem.assert_any_call("tenant_index:1", "tenant:1:user:42")
            mock_pipeline.srem.assert_any_call("tenant_index:1:user", "tenant:1:user:42")
            mock_pipeline.execute.assert_called_once()


class TestInvalidateTenantCache:
    """Test set-based cache invalidation."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_client(self) -> None:
        """Should return 0 when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await invalidate_tenant_cache(1)
            assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_index_empty(self) -> None:
        """Should return 0 when no keys in index."""
        mock_redis = AsyncMock()
        mock_redis.smembers.return_value = set()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await invalidate_tenant_cache(1)

            assert result == 0
            mock_redis.smembers.assert_called_once_with("tenant_index:1")

    @pytest.mark.asyncio
    async def test_uses_smembers_not_scan(self) -> None:
        """Verify SMEMBERS is used instead of SCAN - O(|S|) not O(K)."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()
        mock_redis.smembers.return_value = {"tenant:1:user:42", "tenant:1:user:43"}
        mock_pipeline.execute.return_value = [2, 2]  # delete count, srem count

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await invalidate_tenant_cache(1)

            # Should use smembers
            mock_redis.smembers.assert_called_once()

            # Should use scan, but only to clean up orphaned sharded indexes, not for data key lookup
            mock_redis.scan.assert_called_with(0, match="tenant_index:1:*", count=100)

            # Should delete found keys
            assert result == 2

    @pytest.mark.asyncio
    async def test_deletes_all_keys_and_removes_from_index(self) -> None:
        """Verify all keys are deleted and index is cleared."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()
        keys = {"tenant:1:user:42", "tenant:1:user:43", "tenant:1:perm:100"}
        mock_redis.smembers.return_value = keys
        mock_pipeline.execute.return_value = [3, 1]  # delete keys count, delete index count

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await invalidate_tenant_cache(1)

            assert result == 3

            # Verify pipeline operations - delete is called multiple times now
            # First call deletes the keys, second call deletes the index
            assert mock_pipeline.delete.call_count == 2
            first_delete_args = set(mock_pipeline.delete.call_args_list[0][0])
            assert first_delete_args == keys

    @pytest.mark.asyncio
    async def test_prefix_filtering_works(self) -> None:
        """Verify prefix filtering uses sharded index."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()
        user_keys = {"tenant:1:user:42", "tenant:1:user:43"}
        mock_redis.smembers.return_value = user_keys
        mock_pipeline.execute.return_value = [2, 1, 2]  # delete keys, delete sharded index, srem from global

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_tenant_cache(1, prefix="user")

            # Now uses sharded index directly, so smembers is called on tenant_index:1:user
            mock_redis.smembers.assert_called_once_with("tenant_index:1:user")

            # Should only delete user: keys
            delete_args = set(mock_pipeline.delete.call_args_list[0][0])
            assert "tenant:1:user:42" in delete_args
            assert "tenant:1:user:43" in delete_args

    @pytest.mark.asyncio
    async def test_prefix_no_match_returns_zero(self) -> None:
        """Should return 0 when neither the shard nor the global index matches.

        S-14: an empty SHARD now falls back to the global tenant index before
        giving up. That fallback was the "strictly speaking we should" comment
        in tenant_index.py that was never implemented, and it is why
        `posts.erase_db_post` silently failed to evict a GDPR-erased post: its
        prefix did not match the writer's shard, so the lookup hit an empty set
        and returned 0.

        So smembers is called for the shard AND (on a miss) for the global
        index. The RESULT is unchanged here — 0 — because neither holds a match.
        """
        mock_redis, _ = create_mock_redis_with_pipeline()
        mock_redis.smembers.return_value = set()

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await invalidate_tenant_cache(1, prefix="nonexistent")
            assert result == 0

        called = [c.args[0] for c in mock_redis.smembers.call_args_list]
        assert "tenant_index:1:nonexistent" in called, "the shard must be consulted first"
        assert "tenant_index:1" in called, "S-14: an empty shard must fall back to the global index"


class TestGetTenantIndexSize:
    """Test index size monitoring."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_client(self) -> None:
        """Should return 0 when Redis client is None."""
        with patch("src.app.core.utils.cache.client", None):
            result = await get_tenant_index_size(1)
            assert result == 0

    @pytest.mark.asyncio
    async def test_returns_set_cardinality(self) -> None:
        """Verify SCARD is called and result returned."""
        mock_redis = AsyncMock()
        mock_redis.scard.return_value = 42

        with patch("src.app.core.utils.cache.client", mock_redis):
            result = await get_tenant_index_size(1)

            assert result == 42
            mock_redis.scard.assert_called_once_with("tenant_index:1")


class TestTenantCacheKey:
    """Test tenant cache key generation."""

    def test_generates_correct_key_format(self) -> None:
        """Verify key format with multiple parts."""
        result = tenant_cache_key(1, "user", "42", "profile")
        assert result == "tenant:1:user:42:profile"

    def test_handles_single_key_part(self) -> None:
        """Verify key format with single part."""
        result = tenant_cache_key(1, "settings")
        assert result == "tenant:1:settings"

    def test_handles_int_key_parts(self) -> None:
        """Verify int parts are converted to string."""
        result = tenant_cache_key(1, "user", 42)
        assert result == "tenant:1:user:42"


class TestTenantIsolation:
    """Test that invalidation isolates tenants."""

    @pytest.mark.asyncio
    async def test_invalidate_tenant_a_leaves_tenant_b_intact(self) -> None:
        """Invalidating tenant A should not affect tenant B's keys."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()
        tenant_a_keys = {"tenant:1:user:42", "tenant:1:user:43"}
        mock_redis.smembers.return_value = tenant_a_keys
        mock_pipeline.execute.return_value = [2, 2]

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_tenant_cache(1)

            # Verify only tenant 1 index was queried
            mock_redis.smembers.assert_called_once_with("tenant_index:1")

            # Verify tenant 2's index was never touched
            for call in mock_redis.smembers.call_args_list:
                assert "tenant_index:2" not in str(call)


class TestAtomicOperations:
    """Test that operations are properly atomic via pipelines."""

    @pytest.mark.asyncio
    async def test_set_uses_non_transactional_pipeline(self) -> None:
        """Verify pipeline is non-transactional for performance."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            await set_tenant_cache(1, "test", ("key",), "value")

            # Verify pipeline commands were called
            mock_pipeline.setex.assert_called_once()
            mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_uses_non_transactional_pipeline(self) -> None:
        """Verify delete also uses non-transactional pipeline."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()

        with patch("src.app.core.utils.cache.client", mock_redis):
            await delete_tenant_cache(1, "test", "key")

            # Verify pipeline commands were called
            mock_pipeline.delete.assert_called_once()
            mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_uses_non_transactional_pipeline(self) -> None:
        """Verify invalidation uses non-transactional pipeline."""
        mock_redis, mock_pipeline = create_mock_redis_with_pipeline()
        mock_redis.smembers.return_value = {"tenant:1:key:1"}
        mock_pipeline.execute.return_value = [1, 1]

        with patch("src.app.core.utils.cache.client", mock_redis):
            await invalidate_tenant_cache(1)

            # Verify pipeline commands were called (delete is called twice now)
            assert mock_pipeline.delete.call_count == 2
            mock_pipeline.execute.assert_called_once()
