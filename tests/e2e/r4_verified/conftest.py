"""E2E test fixtures for R4 verified optimizations.

Provides fixtures for:
- Test client with real database and Redis
- User authentication
- Detailed logging of operations
- Cleanup after tests
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_session_maker
from src.app.core.utils import cache
from src.app.main import app

# Configure detailed logging for E2E tests
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Test tenant slug - must exist in DB
TEST_TENANT_SLUG = "test-tenant-e2e"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session-scoped async fixtures."""
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def ensure_tiers():
    """Ensure tiers and rate limits exist in database."""
    async with async_session_maker() as session:
        # Check if tiers exist
        result = await session.execute(text("SELECT id FROM tier WHERE id IN (1, 2)"))
        existing_ids = [row[0] for row in result.fetchall()]

        if 1 not in existing_ids:
            await session.execute(
                text("INSERT INTO tier (id, name, created_at, updated_at) VALUES (1, 'Free', NOW(), NOW())")
            )
            logger.info("Created Free tier (id=1)")

        if 2 not in existing_ids:
            await session.execute(
                text("INSERT INTO tier (id, name, created_at, updated_at) VALUES (2, 'Pro', NOW(), NOW())")
            )
            logger.info("Created Pro tier (id=2)")

        # Ensure rate limits exist
        # Delete existing to ensure clean slate or insert if missing
        await session.execute(text("DELETE FROM rate_limit WHERE tier_id IN (1, 2)"))

        # Add rate limit for Free tier
        await session.execute(
            text("""
                INSERT INTO rate_limit (tier_id, name, path, "limit", period, created_at, updated_at)
                VALUES (1, 'tier_1_default', '/api/v1/users/me', 50, 3600, NOW(), NOW())
            """)
        )
        # Add rate limit for Pro tier
        await session.execute(
            text("""
                INSERT INTO rate_limit (tier_id, name, path, "limit", period, created_at, updated_at)
                VALUES (2, 'tier_2_default', '/api/v1/users/me', 1000, 3600, NOW(), NOW())
            """)
        )
        logger.info("Updated rate limits for tiers 1 and 2")

        await session.commit()


@pytest.fixture(scope="session")
async def ensure_test_tenant(ensure_tiers):
    """Ensure test tenant exists in database (session-scoped for efficiency)."""
    async with async_session_maker() as session:
        # Check if tenant exists
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": TEST_TENANT_SLUG},
        )
        tenant = result.fetchone()

        if not tenant:
            # Create test tenant - id is SERIAL (auto-increment integer)
            await session.execute(
                text("""
                    INSERT INTO tenants (name, slug, is_active, storage_usage_bytes, file_count, created_at, updated_at)
                    VALUES (:name, :slug, true, 0, 0, NOW(), NOW())
                """),
                {"name": "E2E Test Tenant", "slug": TEST_TENANT_SLUG},
            )
            await session.commit()
            logger.info(f"Created test tenant: {TEST_TENANT_SLUG}")
        else:
            logger.info(f"Test tenant already exists: {TEST_TENANT_SLUG}")


@pytest.fixture
async def db_session(ensure_test_tenant) -> AsyncGenerator[AsyncSession, None]:
    """Provide database session for E2E tests."""
    async with async_session_maker() as session:
        logger.info("Created database session for E2E test")
        yield session
        logger.info("Closing database session")


@pytest.fixture
async def client(ensure_test_tenant) -> AsyncGenerator[AsyncClient, None]:
    """Provide async HTTP client for E2E tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Tenant-ID": TEST_TENANT_SLUG},
    ) as ac:
        logger.info("Created HTTP client for E2E test")
        yield ac
        logger.info("Closed HTTP client")


@pytest.fixture
async def authenticated_user(client: AsyncClient) -> dict[str, Any]:
    """Create and authenticate a test user.

    Returns:
        Dict with user data and access token
    """
    # Create test user - username must be lowercase alphanumeric only
    user_data = {
        "name": "E2E Test User",
        "email": "e2e_test@example.com",
        "password": "TestPassword123!",
        "username": "e2etestuser",
    }

    logger.info(f"Creating test user: {user_data['email']}")

    # Clean up existing user if any
    async with async_session_maker() as session:
        await session.execute(
            text('DELETE FROM "user" WHERE email = :email'),
            {"email": user_data["email"]},
        )
        await session.commit()

    # Register user
    response = await client.post("/api/v1/account/register", json=user_data)
    assert response.status_code == 201, f"Registration failed: {response.text}"
    logger.info(f"User registered successfully: {response.json()}")

    # Assign to Free tier (tier_id=1) explicitely BEFORE login
    async with async_session_maker() as session:
        await session.execute(
            text('UPDATE "user" SET tier_id = 1 WHERE email = :email'),
            {"email": user_data["email"]},
        )
        await session.commit()
        logger.info(f"User assigned to Free tier: {user_data['email']}")

        # Verify DB state
        result = await session.execute(
            text('SELECT tier_id FROM "user" WHERE email = :email'), {"email": user_data["email"]}
        )
        curr_tier = result.scalar()
        logger.info(f"DB Verification - User tier_id: {curr_tier}")

    # Invalidate auth cache to ensure login fetches fresh tier info
    if getattr(cache, "client", None):
        logger.info("Invalidating auth cache via cache.client")
        await cache.client.delete(f"user:auth:{user_data['email']}")
        await cache.client.delete(f"user:auth:{user_data['username']}")
    else:
        logger.warning("cache.client is None, skipping cache invalidation")

    # Login to get token
    # OAuth2PasswordRequestForm requires form data with 'username' and 'password' fields
    login_data = {"username": user_data["email"], "password": user_data["password"]}
    response = await client.post("/api/v1/account/login", data=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"

    result = response.json()
    access_token = result["access_token"]

    # Debug: log token claims
    try:
        import jwt

        decoded = jwt.decode(access_token, options={"verify_signature": False})
        logger.info(f"Token claims: {decoded}")
    except Exception as e:
        logger.warning(f"Could not decode token: {e}")

    return {
        "email": user_data["email"],
        "username": user_data["username"],
        "access_token": access_token,
        "user_id": result.get("user_id"),
    }


@pytest.fixture
async def premium_user(client: AsyncClient) -> dict[str, Any]:
    """Create and authenticate a premium tier user.

    Returns:
        Dict with user data and access token
    """
    # Create test user
    user_data = {
        "name": "Premium E2E User",
        "email": "premium_e2e@example.com",
        "password": "PremiumPass123!",
        "username": "premiumtestuser",
    }

    logger.info(f"Creating premium test user: {user_data['email']}")

    # Clean up existing user if any
    async with async_session_maker() as session:
        await session.execute(
            text('DELETE FROM "user" WHERE email = :email'),
            {"email": user_data["email"]},
        )
        await session.commit()

    # Register user
    response = await client.post("/api/v1/account/register", json=user_data)
    assert response.status_code == 201, f"Registration failed: {response.text}"

    # Upgrade to premium tier (tier_id=2) BEFORE login
    async with async_session_maker() as session:
        await session.execute(
            text('UPDATE "user" SET tier_id = 2 WHERE email = :email'),
            {"email": user_data["email"]},
        )
        await session.commit()
        logger.info(f"User assigned to Free tier: {user_data['email']}")

        # Verify DB state
        result = await session.execute(
            text('SELECT tier_id FROM "user" WHERE email = :email'), {"email": user_data["email"]}
        )
        curr_tier = result.scalar()
        logger.info(f"DB Verification - User tier_id: {curr_tier}")

    # Invalidate auth cache to ensure login fetches fresh tier info
    if getattr(cache, "client", None):
        logger.info("Invalidating auth cache via cache.client")
        await cache.client.delete(f"user:auth:{user_data['email']}")
        await cache.client.delete(f"user:auth:{user_data['username']}")
    else:
        logger.warning("cache.client is None, skipping cache invalidation")

    # Login to get token
    # OAuth2PasswordRequestForm requires form data with 'username' and 'password' fields
    login_data = {"username": user_data["email"], "password": user_data["password"]}
    response = await client.post("/api/v1/account/login", data=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"

    result = response.json()
    access_token = result["access_token"]

    # Debug: log token claims
    try:
        import jwt

        decoded = jwt.decode(access_token, options={"verify_signature": False})
        logger.info(f"Token claims: {decoded}")
    except Exception as e:
        logger.warning(f"Could not decode token: {e}")

    return {
        "email": user_data["email"],
        "username": user_data["username"],
        "access_token": access_token,
        "user_id": result.get("user_id"),
        "tier_id": 2,
    }


@pytest.fixture(autouse=True)
async def cleanup_redis():
    """Clean up Redis test data after each test."""
    yield
    if cache.client:
        logger.info("Cleaning up Redis test data")
        # Clean up test-related keys
        keys_to_delete = []
        async for key in cache.client.scan_iter(match="*e2e_test*"):
            keys_to_delete.append(key)
        async for key in cache.client.scan_iter(match="*premium_e2e*"):
            keys_to_delete.append(key)

        if keys_to_delete:
            await cache.client.delete(*keys_to_delete)
            logger.info(f"Deleted {len(keys_to_delete)} Redis keys")


@pytest.fixture
def log_step():
    """Fixture to log test steps with timing."""
    import time

    def _log_step(step: str, **kwargs):
        timestamp = time.time()
        extra_info = " | ".join(f"{k}={v}" for k, v in kwargs.items())
        logger.info(f"[STEP] {step} | timestamp={timestamp:.3f} | {extra_info}")

    return _log_step


@pytest.fixture(autouse=True)
def synchronous_event_bus():
    """Force EventBus to execute handlers synchronously for tests.

    This prevents 'Task was destroyed but it is pending' errors when
    background tasks (like audit logs) try to run after test completion.
    """
    from unittest.mock import patch

    from src.app.core.config import settings
    from src.app.core.events import event_bus

    # Enable GCRA algorithm for tests to get Retry-After headers
    patcher = patch.object(settings, "RATE_LIMIT_ALGORITHM", "gcra")
    patcher.start()

    original_publish_sync = event_bus._publish_sync

    async def _publish_sync_blocking(event_name, data, handlers):
        """Execute handlers immediately and await them."""
        for handler in handlers:
            try:
                await handler(data)
            except Exception as e:
                logger.error(f"Event handler failed in test: {e}")

    # Patch the method
    event_bus._publish_sync = _publish_sync_blocking
    yield
    # Restore original
    event_bus._publish_sync = original_publish_sync
    patcher.stop()
