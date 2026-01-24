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
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_session_maker
from src.app.core.utils.cache import client as redis_client
from src.app.main import app

# Configure detailed logging for E2E tests
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide database session for E2E tests."""
    async with async_session_maker() as session:
        logger.info("Created database session for E2E test")
        yield session
        logger.info("Closing database session")


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide async HTTP client for E2E tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-e2e"},
    ) as ac:
        logger.info("Created HTTP client for E2E test")
        yield ac
        logger.info("Closed HTTP client")


@pytest.fixture
async def authenticated_user(client: AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    """Create and authenticate a test user.

    Returns:
        Dict with user data and access token
    """
    # Create test user
    user_data = {
        "email": "e2e_test@example.com",
        "password": "TestPassword123!",
        "username": "e2e_testuser",
    }

    logger.info(f"Creating test user: {user_data['email']}")

    # Register user
    response = await client.post("/api/v1/account/register", json=user_data)
    assert response.status_code == 201, f"Registration failed: {response.text}"
    logger.info(f"User registered successfully: {response.json()}")

    # Login to get token
    login_data = {"email": user_data["email"], "password": user_data["password"]}
    response = await client.post("/api/v1/account/login", json=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"

    result = response.json()
    access_token = result["access_token"]
    logger.info(f"User authenticated, token: {access_token[:20]}...")

    return {
        "email": user_data["email"],
        "username": user_data["username"],
        "access_token": access_token,
        "user_id": result.get("user_id"),
    }


@pytest.fixture
async def premium_user(client: AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    """Create and authenticate a premium tier user.

    Returns:
        Dict with user data and access token
    """
    # Create test user
    user_data = {
        "email": "premium_e2e@example.com",
        "password": "PremiumPass123!",
        "username": "premium_testuser",
    }

    logger.info(f"Creating premium test user: {user_data['email']}")

    # Register user
    response = await client.post("/api/v1/account/register", json=user_data)
    assert response.status_code == 201, f"Registration failed: {response.text}"

    # Login to get token
    login_data = {"email": user_data["email"], "password": user_data["password"]}
    response = await client.post("/api/v1/account/login", json=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"

    result = response.json()
    access_token = result["access_token"]

    # Upgrade to premium tier (tier_id=2)
    from src.app.modules.users.crud import user_crud

    user = await user_crud.get(db=db_session, email=user_data["email"])
    if user:
        await user_crud.update(db=db_session, object=user, tier_id=2)
        await db_session.commit()
        logger.info(f"User upgraded to premium tier: {user.email}")

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
    if redis_client:
        logger.info("Cleaning up Redis test data")
        # Clean up test-related keys
        keys_to_delete = []
        async for key in redis_client.scan_iter(match="*e2e_test*"):
            keys_to_delete.append(key)
        async for key in redis_client.scan_iter(match="*premium_e2e*"):
            keys_to_delete.append(key)

        if keys_to_delete:
            await redis_client.delete(*keys_to_delete)
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
