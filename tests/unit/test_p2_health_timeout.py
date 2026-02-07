import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from src.app.core import health


@pytest.mark.asyncio
async def test_check_database_health_timeout():
    """Verify database health check times out."""
    mock_db = AsyncMock()
    # Simulate hang logic isn't needed if we patch wait_for, but for completeness
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(6.0)
        return 1
    mock_db.execute.side_effect = side_effect

    # Patch asyncio.wait_for to raise TimeoutError
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await health.check_database_health(mock_db)

        # Should return unhealthy status, NOT raise exception
        assert result.status == "unhealthy"
        assert "Timeout after" in str(result.error)

@pytest.mark.asyncio
async def test_check_redis_health_timeout():
    """Verify redis health check times out."""
    mock_redis = AsyncMock()

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await health.check_redis_health(mock_redis)

        assert result.status == "unhealthy"
        assert "Timeout after" in str(result.error)

@pytest.mark.asyncio
async def test_check_redis_queue_health_timeout():
    """Verify redis queue health check times out."""
    # Mock pool in utils.queue
    mock_pool = AsyncMock()

    with patch("src.app.core.utils.queue.pool", mock_pool):
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await health.check_redis_queue_health()

            assert result.status == "unhealthy"
            assert "Timeout after" in str(result.error)

@pytest.mark.asyncio
async def test_check_database_health_success():
    """Verify success path."""
    mock_db = AsyncMock()

    # We need to unpatch wait_for or use a different scope, but assuming clean env per test function
    # The real asyncio.wait_for will run
    mock_db.execute.return_value = 1

    result = await health.check_database_health(mock_db)
    assert result.status == "healthy"
    assert result.latency_ms is not None

@pytest.mark.asyncio
async def test_check_database_health_operational_error():
    """Verify operational error handling."""
    mock_db = AsyncMock()
    mock_db.execute.side_effect = OperationalError("statement", {}, Exception("Connection refused"))

    result = await health.check_database_health(mock_db)
    assert result.status == "unhealthy"
    assert "OperationalError" in str(result.error)
