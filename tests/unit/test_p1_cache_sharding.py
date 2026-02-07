from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_tenant_sharded_invalidation_logic():
    # Mock Redis client
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # 1. Test set_tenant_cache (Sharded)
    with patch("src.app.core.utils.cache.client", mock_client):
        await cache.set_tenant_cache(tenant_id=1, prefix="user", key_parts=("profile",), value="data")

        # Should add to TWO sets: global index and prefix index
        # sadd calls:
        # 1. tenant_index:1 (global) -> tenant:1:user:profile
        # 2. tenant_index:1:user (sharded) -> tenant:1:user:profile
        assert mock_pipeline.sadd.call_count == 2
        calls = mock_pipeline.sadd.call_args_list
        # verify args
        indices = [c[0][0] for c in calls]
        assert "tenant_index:1" in indices
        assert "tenant_index:1:user" in indices

    # 2. Test invalidate_tenant_cache with prefix (Optimization)
    mock_pipeline.reset_mock()
    mock_client.smembers = AsyncMock(return_value={b"tenant:1:user:profile"})
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1])

    with patch("src.app.core.utils.cache.client", mock_client):
        # Invalidate "user" prefix
        await cache.invalidate_tenant_cache(tenant_id=1, prefix="user")

        # Should fetch from prefix index "tenant_index:1:user"
        mock_client.smembers.assert_called_with("tenant_index:1:user")

        # Should remove from global index too
        mock_pipeline.srem.assert_called_with("tenant_index:1", b"tenant:1:user:profile")

        # Should delete the keys
        mock_pipeline.delete.assert_any_call(b"tenant:1:user:profile")

    # 3. Test invalidate_tenant_cache FULL (no prefix)
    mock_pipeline.reset_mock()
    mock_client.smembers = AsyncMock(return_value={b"tenant:1:user:profile", b"tenant:1:settings:main"})

    with patch("src.app.core.utils.cache.client", mock_client):
        await cache.invalidate_tenant_cache(tenant_id=1)

        # Should fetch from global index "tenant_index:1"
        mock_client.smembers.assert_called_with("tenant_index:1")

        # Should delete keys
        # pipe.delete(*keys) is called with multiple keys
        # Verify that delete was called with the keys we expect
        delete_calls = mock_pipeline.delete.call_args_list
        # We expect at least one delete call for the keys, and one for the index

        # Find the call that deletes the keys (it should have the keys as args)
        keys_deleted = False
        for call in delete_calls:
            args = call[0]
            if b"tenant:1:user:profile" in args and b"tenant:1:settings:main" in args:
                keys_deleted = True
                break

        assert keys_deleted, "Keys were not passed to delete command"

        # Should delete global index
        mock_pipeline.delete.assert_any_call("tenant_index:1")

        # NOTE: It does NOT delete prefix indexes explicitly in full invalidation
        # (based on typical implementation choice to save scans),
        # or it might attempt to if we want to be clean.
        # But our plan was to just clear the data.
