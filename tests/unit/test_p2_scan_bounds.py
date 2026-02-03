import pytest
from unittest.mock import AsyncMock, patch
from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_migrate_tenant_cache_index_scan_bounds():
    """Verify migration stops at MAX_ITERATIONS and logs warning."""
    mock_client = AsyncMock()

    # infinite loop of cursors if not bounded
    # We want to check it stops at 1000
    # return cursor 1 (not 0) forever
    mock_client.scan.return_value = (1, ["key"])

    with patch.object(cache, "client", mock_client), patch("src.app.core.utils.cache.stampede_logger") as mock_logger:
        result = await cache.migrate_tenant_cache_index()

        # Should have called scan 1000 times (MAX_ITERATIONS)
        assert mock_client.scan.call_count == 1000

        # Should log warning
        mock_logger.warning.assert_called_with(
            "SCAN iteration limit reached in migrate_tenant_cache_index",
            extra={"pattern": "tenant:*", "iterations": 1000},
        )


@pytest.mark.asyncio
async def test_delete_keys_by_pattern_scan_bounds():
    """Verify deletion stops at MAX_ITERATIONS and logs warning."""
    mock_client = AsyncMock()
    mock_client.scan.return_value = (1, ["key"])

    with patch.object(cache, "client", mock_client), patch("src.app.core.utils.cache.stampede_logger") as mock_logger:
        await cache._delete_keys_by_pattern("pattern")

        assert mock_client.scan.call_count == 1000

        mock_logger.warning.assert_called_with(
            "SCAN iteration limit reached in _delete_keys_by_pattern",
            extra={"pattern": "pattern", "iterations": 1000},
        )


@pytest.mark.asyncio
async def test_scan_stops_on_zero_cursor():
    """Verify scan stops when cursor is 0."""
    mock_client = AsyncMock()
    # Returns cursor 0 immediately
    mock_client.scan.return_value = (0, ["key"])

    with patch.object(cache, "client", mock_client):
        await cache._delete_keys_by_pattern("pattern")
        assert mock_client.scan.call_count == 1
