"""Performance Phase 1 tests - consolidated from multiple small test files.

Tests for:
- DB config and timeout handling
- Event bus parallel execution
- User cache pipeline optimization
- XFetch RNG optimization
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.pool import NullPool
from uuid6 import uuid7

from src.app.core.config import settings
from src.app.core.db.database import async_engine
from src.app.core.errors import setup_exception_handlers
from src.app.core.events import EventBus, EventPriority
from src.app.core.utils.cache import get_with_stampede_protection
from src.app.modules.users.models import User


# ==================== DB Config ====================


@pytest.mark.asyncio
async def test_db_pool_timeout_config():
    """Test that database engine has correct pool timeout configuration."""
    # Access the underlying pool
    pool = async_engine.pool

    # Skip if using NullPool (SQLite in tests)
    if isinstance(pool, NullPool):
        pytest.skip("NullPool does not support pool configuration (SQLite test mode)")

    # Verify pool settings
    assert pool.size() == settings.DB_POOL_SIZE
    assert pool._max_overflow == settings.DB_MAX_OVERFLOW

    # Verify timeout matches settings
    assert hasattr(settings, "DB_POOL_TIMEOUT")
    assert pool._timeout == settings.DB_POOL_TIMEOUT
    assert pool._timeout == 30  # Verify default value


# ==================== DB Timeout Handler ====================


@pytest.fixture
def app():
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/db-timeout")
    async def db_timeout_endpoint():
        raise SQLAlchemyTimeoutError("QueuePool limit of size 5 overflow 10 reached, connection timed out", None, None)

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_db_timeout_handler(client):
    """Test that SQLAlchemy TimeoutError returns 503 Service Unavailable."""
    response = client.get("/db-timeout")

    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "Database connection pool exhausted" in data["error"]["message"]


# ==================== Event Bus Parallel ====================


@pytest.fixture
def fresh_event_bus():
    """Fresh EventBus instance for testing."""
    return EventBus()


@pytest.mark.asyncio
async def test_parallel_execution_performance(fresh_event_bus):
    """Verify that handlers run in parallel.

    In sequential execution, 2 handlers taking 0.1s each will take ~0.2s total.
    In parallel execution, they should take ~0.1s total.
    """
    delay = 0.1

    @fresh_event_bus.subscribe("parallel.test")
    async def handler1(data: dict):
        await asyncio.sleep(delay)
        data["handler1"] = time.time()

    @fresh_event_bus.subscribe("parallel.test")
    async def handler2(data: dict):
        await asyncio.sleep(delay)
        data["handler2"] = time.time()

    start_time = time.time()
    data = {}

    # CRITICAL priority uses _publish_sync which is what we are targeting
    await fresh_event_bus.publish("parallel.test", data, priority=EventPriority.CRITICAL)
    end_time = time.time()

    duration = end_time - start_time

    # Assert it takes less than the sum of delays (indicating parallelism)
    # If sequential, it would be >= 0.2
    assert duration < (delay * 1.5), f"Execution took {duration}s, expected < {delay * 1.5}s"


@pytest.mark.asyncio
async def test_parallel_exception_isolation(fresh_event_bus):
    """Verify one failure doesn't stop others in parallel mode."""
    # Use a dict to store results - more reliable than list append in async context
    results = {"handlers_completed": 0, "success1": False, "success2": False}

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_success_1(data: dict):
        results["success1"] = True
        results["handlers_completed"] += 1

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_fail(data: dict):
        raise ValueError("Boom")

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_success_2(data: dict):
        results["success2"] = True
        results["handlers_completed"] += 1

    await fresh_event_bus.publish("parallel.error", {}, priority=EventPriority.CRITICAL)

    # _publish_sync uses asyncio.create_task() for fire-and-forget execution
    # We need to yield control to allow background tasks to complete
    await asyncio.sleep(0.05)

    # Both success handlers should complete despite the failure
    assert results["success1"], "handler_success_1 should have completed"
    assert results["success2"], "handler_success_2 should have completed"
    assert results["handlers_completed"] == 2, "Both success handlers should complete"


# ==================== User Cache Pipeline ====================


@pytest.mark.asyncio
async def test_cache_user_uses_pipeline():
    """Test that _cache_user uses a Redis pipeline for optimization."""
    from src.app.modules.users.service import (
        UserService,
        _user_cache_key,
        _user_email_cache_key,
        _user_username_cache_key,
    )

    # Setup
    mock_db = AsyncMock()

    # Create user with UUID tenant_id
    tenant_id = uuid7()
    user = MagicMock(spec=User)
    user.id = 1  # Must be int
    user.email = "test@example.com"
    user.username = "testuser"
    user.name = "Test User"
    user.is_deleted = False
    user.is_superuser = False
    user.is_email_verified = True
    user.profile_image_url = "https://example.com/profile.jpg"
    user.hashed_password = "hash"
    user.created_at = MagicMock()
    user.created_at.isoformat.return_value = "2023-01-01T00:00:00"
    user.updated_at = None
    user.tenant_id = tenant_id
    user.tier_id = 1

    # Mock tenant context
    mock_tenant = MagicMock()
    mock_tenant.id = tenant_id

    # Mock cache with pipeline
    mock_cache = MagicMock()
    mock_redis = MagicMock()
    mock_cache.client = mock_redis

    # Create mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock()
    mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
    mock_pipeline.__aexit__ = AsyncMock(return_value=None)
    mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

    # Patch both the context module and the service's imported reference
    with patch("src.app.core.tenancy.context.get_current_tenant", return_value=mock_tenant):
        with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
            with patch("src.app.modules.users.service.cache", mock_cache):
                service = UserService(mock_db)
                await service._cache_user(user)

                # VERIFICATION
                mock_redis.pipeline.assert_called_once_with(transaction=False)
                assert mock_pipeline.setex.call_count == 3
                mock_pipeline.execute.assert_called_once()

                # Check keys - must be inside patch context for tenant-aware key functions
                calls = mock_pipeline.setex.call_args_list
                keys = [call.args[0] for call in calls]
                assert _user_cache_key(user.id) in keys
                assert _user_email_cache_key(user.email) in keys
                assert _user_username_cache_key(user.username) in keys


# ==================== XFetch RNG ====================


@pytest.mark.asyncio
async def test_xfetch_uses_random_not_secrets():
    """Verify XFetch uses random.random() instead of secrets.randbelow()."""

    # Mock Redis
    mock_redis = AsyncMock()
    # Cache hit
    mock_redis.get.return_value = b'{"data": "old"}'
    # TTL is low enough to trigger probability check
    # If ttl=3600, remaining=100, delta=3500. prob = 1.0 * (3500/3600)^2 ~= 0.94
    mock_redis.ttl.return_value = 100

    fetch_func = AsyncMock(return_value={"data": "new"})

    with patch("src.app.core.utils.cache.client", mock_redis):
        # Disable Lua script to use the get/ttl path
        with patch("src.app.core.utils.cache.settings.XFETCH_USE_LUA_SCRIPT", False):
            # We only expect random to be present and used now
            with patch("src.app.core.utils.cache.random.random") as mock_random:
                mock_random.return_value = 0.0  # Force trigger if used

                # Patch _refresh_cache to avoid actual background task execution issues
                with patch("src.app.core.utils.cache._refresh_cache"):
                    await get_with_stampede_protection("key", fetch_func, ttl=3600)

                    # Assert random.random was called
                    assert mock_random.called
                    print("Used random.random")
