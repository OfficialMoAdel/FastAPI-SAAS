import pytest

from src.app.core.config import settings
from src.app.core.db.database import async_engine


@pytest.mark.asyncio
async def test_db_pool_timeout_config():
    """Test that database engine has correct pool timeout configuration."""
    # Access the underlying pool
    pool = async_engine.pool

    # Verify pool settings
    assert pool.size() == settings.DB_POOL_SIZE
    assert pool._max_overflow == settings.DB_MAX_OVERFLOW

    # Verify timeout matches settings
    assert hasattr(settings, "DB_POOL_TIMEOUT")
    assert pool._timeout == settings.DB_POOL_TIMEOUT
    assert pool._timeout == 30  # Verify default value
