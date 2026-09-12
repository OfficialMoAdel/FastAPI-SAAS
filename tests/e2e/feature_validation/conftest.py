"""E2E test fixtures for R4 verified optimizations.

Provides fixtures for:
- Test client with real database and Redis
- User authentication
- Detailed logging of operations
- Cleanup after tests
"""

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

# Disable bad bot filter for tests (httpx is used by test clients)
# MUST be set before importing settings, which reads env vars at module load
os.environ["BAD_BOT_FILTER_ENABLED"] = "false"
# P0-1 fixed: attack detection no longer consumes request bodies, so the
# middleware stays enabled in tests (see tests/conftest.py).

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.app.core.config import settings
from src.app.core.db.database import Base
from src.app.core.security.encryption import get_encryption
from src.app.core.utils import cache

# Reset cached bad bot regex so it picks up the disabled setting
from src.app.middleware.ip_filter import _reset_bad_bot_regex

_reset_bad_bot_regex()

# Configure detailed logging for E2E tests
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Test tenant slug - must exist in DB
TEST_TENANT_SLUG = "test-tenant-e2e"


def _get_test_session_maker():
    """Create a test-specific async session maker with NullPool.

    NullPool prevents connection caching across event loops, which is
    critical for function-scoped pytest-asyncio tests.
    """
    test_db_url = f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_URI}"
    engine = create_async_engine(
        test_db_url,
        poolclass=NullPool,  # Critical: fresh connection per use
    )
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="function", autouse=True)
async def ensure_real_postgres_schema():
    """Ensure real Postgres has all ORM tables before each test.

    Uses checkfirst semantics via SQLAlchemy metadata create_all to avoid
    failures when tests run against stale DB schemas after migrations.
    """

    test_db_url = f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_URI}"
    engine = create_async_engine(test_db_url, poolclass=NullPool)
    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            # Fully reset schema to avoid index collisions from stray tables
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
            await conn.run_sync(Base.metadata.create_all)
        yield
    finally:
        await engine.dispose()


# NOTE: event_loop fixture REMOVED - pytest-asyncio 0.23+ handles this via
# pyproject.toml settings (asyncio_default_fixture_loop_scope = "function").
# Custom event_loop fixtures conflict with the global config and cause
# test isolation failures during coverage runs.


@pytest.fixture(scope="function")
async def ensure_tiers():
    """Ensure tiers and rate limits exist in database."""
    async with _get_test_session_maker()() as session:
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
        # Add rate limit for file uploads for Free tier (for E2E tests)
        await session.execute(
            text("""
                INSERT INTO rate_limit (tier_id, name, path, "limit", period, created_at, updated_at)
                VALUES (1, 'tier_1_files', '/api/v1/files/upload', 100, 3600, NOW(), NOW())
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


@pytest.fixture(scope="function")
async def ensure_test_tenant(ensure_tiers):
    """Ensure test tenant exists in database (function-scoped for loop compatibility)."""
    async with _get_test_session_maker()() as session:
        # Check if tenant exists
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": TEST_TENANT_SLUG},
        )
        tenant = result.fetchone()

        tenant_id = None
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

            # Fetch ID
            result = await session.execute(
                text("SELECT id FROM tenants WHERE slug = :slug"),
                {"slug": TEST_TENANT_SLUG},
            )
            tenant_id = result.scalar()
        else:
            logger.info(f"Test tenant already exists: {TEST_TENANT_SLUG}")
            tenant_id = tenant[0]

        # Ensure storage settings exist with valid credentials
        os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        credentials = {"base_path": "./storage_test"}
        encryption = get_encryption()
        encrypted = encryption.encrypt_credential(json.dumps(credentials).encode())

        await session.execute(
            text("""
                INSERT INTO storage_settings (tenant_id, provider, max_file_size_mb, max_file_count, storage_quota_bytes, credentials_encrypted, created_at, updated_at)
                VALUES (:tenant_id, 'local', 100, 10000, 10737418240, :encrypted, NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE
                SET credentials_encrypted = EXCLUDED.credentials_encrypted,
                    provider = 'local'
            """),  # noqa: E501
            {"tenant_id": tenant_id, "encrypted": encrypted},
        )
        await session.commit()
        logger.info(f"Ensured storage settings for test tenant: {TEST_TENANT_SLUG}")


@pytest.fixture
async def db_session(ensure_test_tenant) -> AsyncGenerator[AsyncSession, None]:
    """Provide database session for E2E tests."""
    async with _get_test_session_maker()() as session:
        logger.info("Created database session for E2E test")
        yield session
        logger.info("Closing database session")


@pytest.fixture
async def client(ensure_test_tenant) -> AsyncGenerator[AsyncClient, None]:
    """Provide async HTTP client for E2E tests with initialized Redis."""
    from redis import asyncio as aioredis

    from src.app.core.security.session_manager import session_manager
    from src.app.core.utils.gcra_rate_limit import init_gcra_rate_limiter
    from src.app.main import app

    # Reset globals to avoid cross-loop resource reuse during coverage runs
    cache.client = None
    session_manager.set_redis_client(None)

    # Initialize Redis client for session manager (ASGITransport doesn't trigger lifespan)
    redis_client = aioredis.from_url(
        settings.REDIS_CACHE_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    session_manager.set_redis_client(redis_client)
    logger.info("Initialized Redis client for session manager")

    # Set cache.client for other Redis operations
    cache.client = redis_client
    logger.info("Initialized cache.client for Redis operations")

    # CRITICAL: Reinitialize GCRA rate limiter with new Redis client
    # The global rate limiter may have a stale Redis connection from a previous test
    init_gcra_rate_limiter(redis_client)
    logger.info("Initialized GCRA rate limiter")

    # CRITICAL: Clear rate limits BEFORE any test fixtures run
    # This prevents rate limit accumulation across test runs
    logger.info("Pre-test rate limit cleanup")
    pre_patterns = [
        "ratelimit:*",
        "gcra:*",
        "user:auth:*",
        "user_sessions_v2:*",
        "user_sessions_order:*",
        # Tenant resolution caches. The test tenant row can be recreated with a
        # NEW UUID between runs, but the middleware caches slug->tenant_id in
        # Redis (1h TTL) and an in-process L1 TTLCache. A stale cached id makes
        # the middleware set the wrong tenant context, so user lookups filter on
        # a tenant_id that no longer matches the DB row and login fails 401.
        "tenant:slug:*",
        "tenant:negative:*",
    ]
    for pattern in pre_patterns:
        try:
            keys = []
            async for key in cache.client.scan_iter(match=pattern):
                keys.append(key)
            if keys:
                await cache.client.delete(*keys)
                logger.info(f"Pre-cleaned {len(keys)} keys matching: {pattern}")
        except Exception as e:
            logger.warning(f"Pre-cleanup failed for {pattern}: {e}")

    # Also clear the middleware's in-process L1 tenant cache (same process as the
    # app under ASGITransport), so a stale slug->tenant_id mapping cannot survive
    # even when Redis was already clean.
    try:
        from src.app.middleware.tenant import _L1_TENANT_CACHE

        _L1_TENANT_CACHE.clear()
        logger.info("Cleared middleware L1 tenant cache")
    except Exception as e:
        logger.warning(f"Failed to clear L1 tenant cache: {e}")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Tenant-ID": TEST_TENANT_SLUG},
    ) as ac:
        logger.info("Created HTTP client for E2E test")
        yield ac
        logger.info("Closed HTTP client")

    # Cleanup
    await redis_client.aclose()
    cache.client = None
    session_manager.set_redis_client(None)

    # Clear global limiter singleton to avoid stale loop/client binding
    import src.app.core.utils.gcra_rate_limit as gcra_module

    gcra_module.gcra_rate_limiter = None
    logger.info("Closed Redis client")


@pytest.fixture
async def authenticated_user(client: AsyncClient, ensure_test_tenant) -> dict[str, Any]:
    """Create and authenticate a test user.

    Creates user directly in DB to bypass tenant middleware exemption on /register.

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

    # Get tenant ID first
    async with _get_test_session_maker()() as session:
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": TEST_TENANT_SLUG},
        )
        tenant_id = result.scalar()
        logger.info(f"Found tenant_id: {tenant_id} for slug: {TEST_TENANT_SLUG}")

        # Clean up existing user if any
        await session.execute(
            text("DELETE FROM users WHERE email = :email OR username = :username"),
            {"email": user_data["email"], "username": user_data["username"]},
        )
        await session.commit()

        # Create user directly in DB with explicit tenant_id and all required fields
        from uuid6 import uuid7

        from src.app.core.security import get_password_hash

        hashed_password = await get_password_hash(user_data["password"])

        insert_result = await session.execute(
            text("""
                INSERT INTO users (
                    name, email, username, hashed_password, tenant_id, tier_id,
                    profile_image_url, uuid, is_active, is_deleted, is_superuser,
                    is_email_verified, token_version, created_at, updated_at
                )
                VALUES (
                    :name, :email, :username, :password, :tenant_id, 1,
                    'https://profileimageurl.com', :uuid, true, false, true,
                    false, 0, NOW(), NOW()
                )
                RETURNING id
            """),
            {
                "name": user_data["name"],
                "email": user_data["email"],
                "username": user_data["username"],
                "password": hashed_password,
                "tenant_id": tenant_id,
                "uuid": uuid7(),
            },
        )
        user_id = insert_result.scalar()

        # Assign admin role explicitly
        role_result = await session.execute(
            text("SELECT id FROM roles WHERE tenant_id = :tenant_id AND name = 'admin'"), {"tenant_id": tenant_id}
        )
        role_id = role_result.scalar()

        if user_id and role_id:
            await session.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id)"),
                {"user_id": user_id, "role_id": role_id},
            )

        await session.commit()
        logger.info(f"Created user directly in DB with tenant_id={tenant_id}")

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
        "refresh_token": response.cookies.get("refresh_token"),
        "user_id": result.get("user_id"),
    }


@pytest.fixture
async def premium_user(client: AsyncClient, ensure_test_tenant) -> dict[str, Any]:
    """Create and authenticate a premium tier user.

    Creates user directly in DB to bypass tenant middleware exemption on /register.

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

    # Get tenant ID first
    async with _get_test_session_maker()() as session:
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": TEST_TENANT_SLUG},
        )
        tenant_id = result.scalar()
        logger.info(f"Found tenant_id: {tenant_id} for slug: {TEST_TENANT_SLUG}")

        # Clean up existing user if any
        await session.execute(
            text("DELETE FROM users WHERE email = :email OR username = :username"),
            {"email": user_data["email"], "username": user_data["username"]},
        )
        await session.commit()

        # Create user directly in DB with explicit tenant_id and tier_id=2 (Pro)
        from uuid6 import uuid7

        from src.app.core.security import get_password_hash

        hashed_password = await get_password_hash(user_data["password"])

        insert_result = await session.execute(
            text("""
                INSERT INTO users (
                    name, email, username, hashed_password, tenant_id, tier_id,
                    profile_image_url, uuid, is_active, is_deleted, is_superuser,
                    is_email_verified, token_version, created_at, updated_at
                )
                VALUES (
                    :name, :email, :username, :password, :tenant_id, 2,
                    'https://profileimageurl.com', :uuid, true, false, true,
                    false, 0, NOW(), NOW()
                )
                RETURNING id
            """),
            {
                "name": user_data["name"],
                "email": user_data["email"],
                "username": user_data["username"],
                "password": hashed_password,
                "tenant_id": tenant_id,
                "uuid": uuid7(),
            },
        )
        user_id = insert_result.scalar()

        # Assign admin role explicitly
        role_result = await session.execute(
            text("SELECT id FROM roles WHERE tenant_id = :tenant_id AND name = 'admin'"), {"tenant_id": tenant_id}
        )
        role_id = role_result.scalar()

        if user_id and role_id:
            await session.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id)"),
                {"user_id": user_id, "role_id": role_id},
            )

        await session.commit()
        logger.info(f"Created premium user directly in DB with tenant_id={tenant_id}, tier_id=2")

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
        "refresh_token": response.cookies.get("refresh_token"),
        "user_id": result.get("user_id"),
        "tier_id": 2,
    }


async def _clear_auth_rate_limits():
    """Clear per-IP auth rate-limit keys from Redis.

    The auth rate limiter allows only 5 ops/minute per IP. E2E tests that
    perform more auth operations than the budget (login bursts, multi-session
    logout) trip HTTP 429 mid-body. Call this before a burst that would exceed
    the per-minute budget.
    """
    redis = getattr(cache, "client", None)
    if redis is None:
        logger.warning("clear_auth_rate_limits: cache.client is None, skipping")
        return
    # Clear ALL rate-limit keys so the next burst starts from a clean budget.
    # Auth limiter may use fixed-window (ratelimit:*) or GCRA (gcra:*) keys
    # depending on the active RATE_LIMIT_ALGORITHM setting.
    total = 0
    for pattern in ("ratelimit:*", "gcra:*"):
        keys = [k async for k in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)
            total += len(keys)
    logger.info(f"clear_auth_rate_limits: cleared {total} rate-limit keys")


@pytest.fixture
def clear_auth_rate_limits():
    """Return a helper to clear per-IP auth rate-limit keys mid-test.

    Use this in tests that perform more than the 5-ops/minute auth budget
    (multi-session login/logout bursts) to avoid HTTP 429 mid-body.
    """
    return _clear_auth_rate_limits


@pytest.fixture(autouse=True)
async def cleanup_redis():
    """Clean up Redis test data before and after each test.

    Cleans all test-related keys including:
    - Test-specific keys (e2e_test, premium_e2e)
    - Rate limit keys (ratelimit:*, gcra:*)
    - Auth cache keys (user:auth:*)
    - Tenant cache keys
    - Token blacklist keys
    """
    # BEFORE test: Clear rate limits to prevent interference
    if cache.client:
        logger.info("Pre-test Redis cleanup")
        pre_patterns = [
            "ratelimit:*",  # Fixed window rate limit keys
            "gcra:*",  # GCRA rate limit keys
            "user:auth:*",  # Auth cache
            "token:verified:*",  # Token negative cache - must clear to avoid stale entries
            "token:blacklist",  # Token blacklist sorted set
        ]
        for pattern in pre_patterns:
            try:
                keys = []
                async for key in cache.client.scan_iter(match=pattern):
                    keys.append(key)
                if keys:
                    await cache.client.delete(*keys)
                    logger.info(f"Pre-cleaned {len(keys)} keys matching: {pattern}")
            except Exception as e:
                logger.warning(f"Pre-cleanup failed for {pattern}: {e}")

    yield

    # AFTER test: Full cleanup
    if cache.client:
        logger.info("Post-test Redis cleanup")

        # Patterns to clean - covers all rate limit and auth keys
        patterns_to_clean = [
            "*e2e_test*",
            "*premium_e2e*",
            "ratelimit:*",  # Fixed window rate limit keys
            "gcra:*",  # GCRA rate limit keys
            "user:auth:*",  # Auth cache
            "tenant:*",  # Tenant cache
            "blacklist:*",  # Token blacklist (legacy pattern)
            "token:blacklist",  # Token blacklist sorted set (actual key)
            "token:verified:*",  # Token negative cache
            "cache:*",  # General cache keys
        ]

        total_deleted = 0
        for pattern in patterns_to_clean:
            keys_to_delete = []
            try:
                async for key in cache.client.scan_iter(match=pattern):
                    keys_to_delete.append(key)

                if keys_to_delete:
                    await cache.client.delete(*keys_to_delete)
                    total_deleted += len(keys_to_delete)
                    logger.info(f"Deleted {len(keys_to_delete)} keys matching pattern: {pattern}")
            except Exception as e:
                logger.warning(f"Failed to clean pattern {pattern}: {e}")

        if total_deleted:
            logger.info(f"Total Redis keys deleted: {total_deleted}")

        # Clear tenant context to prevent leakage between tests
        try:
            from src.app.core.tenancy.context import clear_tenant_context

            clear_tenant_context()
            logger.info("Cleared tenant context")
        except Exception as e:
            logger.warning(f"Failed to clear tenant context: {e}")


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
    patcher = patch.object(settings.rate_limit, "RATE_LIMIT_ALGORITHM", "gcra")
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
