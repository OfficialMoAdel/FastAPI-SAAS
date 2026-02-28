import os
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from faker import Faker
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import Engine, String, create_engine, event
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, INET, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import TextClause
from uuid6 import uuid7

# Load environment variables from src/.env
load_dotenv("src/.env")

# Disable bad bot filter for tests (httpx is used by test clients)
os.environ["BAD_BOT_FILTER_ENABLED"] = "false"

from src.app.core.config import settings

# DO NOT import app globally - it causes state pollution in coverage runs
# from src.app.main import app  # REMOVED - use get_test_app() fixture instead


# SQLite type compilers for PostgreSQL types
@compiles(JSONB, "sqlite")
def compile_jsonb(type_, compiler, **kw):
    return compiler.visit_JSON(type_, **kw)


@compiles(UUID, "sqlite")
def compile_uuid(type_, compiler, **kw):
    return compiler.visit_VARCHAR(type_, **kw)


@compiles(BYTEA, "sqlite")
def compile_bytea(type_, compiler, **kw):
    return compiler.visit_large_binary(type_, **kw)


@compiles(INET, "sqlite")
def compile_inet(type_, compiler, **kw):
    return compiler.visit_VARCHAR(String(45), **kw)


@compiles(ARRAY, "sqlite")
def compile_array(type_, compiler, **kw):
    return compiler.visit_JSON(type_, **kw)


@compiles(TextClause, "sqlite")
def compile_text_clause(element, compiler, **kw):
    text = element.text
    if "current_timestamp(0)" in text:
        text = text.replace("current_timestamp(0)", "CURRENT_TIMESTAMP")
    return text


def gen_random_uuid():
    return str(uuid.uuid4())


@event.listens_for(Engine, "connect")
def do_connect(dbapi_connection, connection_record):
    try:
        dbapi_connection.create_function("gen_random_uuid", 0, gen_random_uuid)
    except Exception:
        pass


# Ensure encryption key is set for tests - must be a valid Fernet key
_test_encryption_key = os.getenv("TEST_ENCRYPTION_KEY")
if not _test_encryption_key:
    _test_encryption_key = Fernet.generate_key().decode()
settings.ENCRYPTION_KEY = SecretStr(_test_encryption_key)

# Use SQLite for tests - fresh database each run, no data conflicts
# We use a file-based DB to allow sharing between sync engine (setup) and async engine (app)
import os

from sqlalchemy.pool import NullPool

_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Use a unique filename to avoid file locking issues on Windows during concurrent runs or rapid re-runs
DB_FILENAME = f"test_{uuid.uuid4().hex}.db"
DATABASE_PATH = os.path.join(_base_dir, DB_FILENAME)

# Format path for SQLite URI
if os.name == "nt":
    DATABASE_URI_STR = f"/{DATABASE_PATH.replace(os.sep, '/')}"
else:
    DATABASE_URI_STR = f"/{DATABASE_PATH}"

print(f"DEBUG: conftest loaded. Using SQLite: {DATABASE_URI_STR}")

# Sync engine for setup (create tables)
sync_engine = create_engine(
    f"sqlite://{DATABASE_URI_STR}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,  # Close connections immediately after use
)
local_session = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

# Async engine for tests - NullPool prevents connection caching across event loops
# This is the key to avoiding "Task got Future attached to a different loop" errors
test_async_engine = create_async_engine(
    f"sqlite+aiosqlite://{DATABASE_URI_STR}",
    poolclass=NullPool,  # Critical: fresh connection per use = no loop binding issues
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Backward compatibility aliases
def get_test_async_engine():
    """Get the test async engine."""
    return test_async_engine


def get_testing_session_local():
    """Get the testing session factory."""
    return TestingSessionLocal


from src.app.core.db.database import Base

# Import all models to ensure Base.metadata is fully populated for drop_all/create_all
# This is critical - SQLAlchemy needs all models imported before create_all()

# 1. Independent / Leaf models (or close to leaf)
from src.app.modules.tiers.models import Tier
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.files.models import File
from src.app.modules.api_keys.models import APIKey
from src.app.modules.notifications.models import Notification, EmailTemplateOverride
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.ai.models import AIChatSession, AIChatMessage, AISettings, AITokenUsage
from src.app.modules.auth.models import PasswordResetToken, EmailVerificationToken
from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.modules.billing.models import (
    StripeSettings,
    SubscriptionPlan,
    Product,
    Pricing,
    UserSubscription,
    BillingRecord,
)
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.magic_link.models import MagicLink
from src.app.modules.mfa.models import MFAMethod
from src.app.modules.passkey.models import PasskeyCredential
from src.app.modules.posts.models import Post
from src.app.modules.sso.models import UserSSOConnection
from src.app.modules.sso_settings.models import SSOSettings
from src.app.modules.storage_settings.models import StorageSettings
from src.app.modules.user_preferences.models import UserPreferences

# 2. Models that depend on above
from src.app.modules.users.models import User

# 3. Models that depend on User/Others (Root)
from src.app.modules.tenants.models import Tenant

# Debug: Print registered tables to verify imports
print(f"DEBUG: Registered tables: {Base.metadata.tables.keys()}")

fake = Faker()


@pytest.fixture(scope="function")
def get_test_app():
    """Get a fresh app instance for each test to prevent state pollution.

    This fixture imports the app lazily to avoid global state issues during
    coverage runs where all test modules are loaded simultaneously.
    """
    from src.app.main import app

    # Clear any existing dependency overrides from previous tests
    app.dependency_overrides.clear()

    yield app

    # Cleanup after test
    app.dependency_overrides.clear()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Ensure fresh database schema for tests."""
    # Delete database if exists to ensure clean state
    try:
        if os.path.exists(DATABASE_PATH):
            os.remove(DATABASE_PATH)
    except Exception:
        pass
        
    # Create all tables
    Base.metadata.create_all(bind=sync_engine)

    yield

    # Cleanup
    sync_engine.dispose()

    # Reset circuit breaker registry to prevent lock binding issues
    from src.app.core.resilience.registry import reset_registry

    reset_registry()
    try:
        if os.path.exists(DATABASE_PATH):
            os.remove(DATABASE_PATH)
    except Exception as e:
        print(f"Warning: Could not remove test database {DATABASE_PATH}: {e}")
    # if os.path.exists(DATABASE_PATH):
    #     try:
    #         os.remove(DATABASE_PATH)
    #     except Exception:
    #         pass


@pytest.fixture(autouse=True)
def reset_circuit_breaker_registry():
    """Reset circuit breaker registry and module-level breakers before each test.

    Prevents asyncio.Lock binding issues when event loops change between tests.
    This is critical for test isolation during coverage runs.
    """
    from src.app.core.resilience.registry import reset_registry
    from src.app.core.utils.circuit_breaker import reset_all_circuit_breakers

    reset_registry()
    reset_all_circuit_breakers()
    yield
    reset_registry()
    reset_all_circuit_breakers()


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singleton instances before and after each test.

    This fixture ensures test isolation by resetting module-level singletons
    that can retain state between tests. Critical for coverage runs where
    tests share the same Python process.

    Singletons reset:
    - RateLimiter (Redis Lua script cache)
    - TokenBlacklistCache (script SHA, locks)
    - EventBus (handlers, registration state)
    """
    # Import reset functions - these are added for test isolation
    from src.app.core.events import reset_event_bus
    from src.app.core.security.token_blacklist_cache import reset_token_blacklist_cache
    from src.app.core.utils.rate_limit import reset_rate_limiter

    # Pre-test reset
    reset_rate_limiter()
    reset_token_blacklist_cache()
    reset_event_bus()

    yield

    # Post-test cleanup
    reset_rate_limiter()
    reset_token_blacklist_cache()
    reset_event_bus()


@pytest.fixture(autouse=True)
async def cleanup_async_tasks():
    """Ensure all async tasks are cancelled after each test.

    Prevents 'Task was destroyed but it is pending' errors when background
    tasks from one test continue running during subsequent tests.

    This is particularly important for tests using EventBus, ARQ, or other
    async background processing.
    """
    import asyncio

    yield

    # Cancel all pending tasks except current
    try:
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current]

        for task in pending:
            task.cancel()

        if pending:
            # Wait for cancellation to complete
            await asyncio.gather(*pending, return_exceptions=True)
    except RuntimeError:
        # No event loop in sync tests
        pass


@pytest.fixture
def test_redis_prefix(request):
    """Generate unique Redis key prefix for each test.

    This prefix can be used to isolate Redis keys between tests,
    preventing key collisions during parallel or batch test runs.

    Usage:
        @pytest.mark.asyncio
        async def test_something(test_redis_prefix):
            key = f"{test_redis_prefix}my_key"
            await redis.set(key, "value")
    """
    return f"test:{request.module.__name__}:{request.node.name}:"


@pytest.fixture
async def isolate_redis_keys(test_redis_prefix):
    """Ensure Redis keys don't collide between tests.

    This fixture provides automatic Redis key isolation by:
    1. Providing a unique prefix for each test
    2. Cleaning up all keys with that prefix after the test

    Usage:
        @pytest.mark.asyncio
        async def test_something(isolate_redis_keys):
            # All keys created with the prefix will be cleaned up
            prefix = isolate_redis_keys
            await redis.set(f"{prefix}my_key", "value")
    """
    from src.app.core.utils import cache

    prefix = test_redis_prefix

    yield prefix

    # Cleanup all keys with this test's prefix
    if cache.client:
        try:
            keys = []
            async for key in cache.client.scan_iter(match=f"{prefix}*"):
                keys.append(key)
            if keys:
                await cache.client.delete(*keys)
        except Exception:
            pass  # Redis may not be available in all tests


@pytest.fixture
def db(setup_db) -> Generator[Session, Any, None]:
    session = local_session()
    yield session
    session.close()


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Override database dependency to use test async session.

    Note: This is a plain async generator, NOT an async context manager.
    FastAPI's Depends() expects an async generator function.
    """
    async with get_testing_session_local()() as session:
        yield session


@pytest.fixture
def client(get_test_app) -> Generator[TestClient, Any, None]:
    """Sync TestClient fixture with standard mocking for general API tests."""
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant"
    fake_tenant.is_active = True

    from src.app.api.dependencies import get_current_user
    from src.app.core.db.database import async_get_db

    # Mock superuser for auth
    fake_superuser = MockUser(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        # tenant_id defaults to DEFAULT_TENANT_ID
    )

    async def mock_get_current_user():
        return fake_superuser

    # Mock for _get_current_user_from_token used by RBAC dependencies
    async def mock_get_current_user_from_token(token: str = None, db=None):
        return fake_superuser

    # Note: We use dependency_overrides for async_get_db because FastAPI
    # needs to properly handle the async generator in dependency injection
    from src.app.core.db.database import async_get_db

    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.middleware.rate_limit.settings.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA rate limit check
        patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token", side_effect=mock_get_current_user_from_token
        ),
        patch("src.app.middleware.tenant.async_engine", new=create_mock_engine()),
    ):
        with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
            yield _client

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(get_test_app) -> Generator[TestClient, Any, None]:
    """Unauthenticated client fixture for testing auth-required endpoints."""
    app = get_test_app

    # Create fake tenant for tenant middleware
    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.core.db.database import async_get_db

    # Override DB but NOT authentication - this is an unauthenticated client
    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        patch("src.app.middleware.rate_limit.settings.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock tenant lookup to return our fake tenant
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch("src.app.middleware.tenant.async_engine", new=create_mock_engine()),
        patch("src.app.middleware.ip_filter.settings.BAD_BOT_FILTER_ENABLED", False),
    ):
        with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
            yield _client

    # Explicit cleanup
    app.dependency_overrides.clear()


@pytest.fixture
def client_auth(get_test_app) -> Generator[TestClient, Any, None]:
    """Client fixture for authentication tests - patches tenant and uses SQLite DB."""
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.api.dependencies import get_current_user
    from src.app.core.db.database import async_get_db

    # Mock superuser with dict-like access for routes using current_user["id"]
    fake_superuser = MockUser(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=MockUser.DEFAULT_TENANT_ID,  # Use UUID
    )

    async def mock_get_current_user():
        return fake_superuser

    async def mock_get_current_user_from_token(token: str = None, db=None):
        return fake_superuser

    # Note: We use dependency_overrides for async_get_db because FastAPI
    # needs to properly handle the async generator in dependency injection
    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.middleware.rate_limit.settings.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA rate limit check
        patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token", side_effect=mock_get_current_user_from_token
        ),
        patch("src.app.middleware.tenant.async_engine", new=create_mock_engine()),
    ):
        with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
            yield _client

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


def override_dependency(dependency: Callable[..., Any], mocked_response: Any, app_instance=None) -> None:
    """Override a FastAPI dependency with a mocked response.

    Args:
        dependency: The dependency function to override
        mocked_response: The mock response to return
        app_instance: Optional app instance (if None, imports lazily)
    """
    if app_instance is None:
        from src.app.main import app

        app_instance = app
    app_instance.dependency_overrides[dependency] = lambda: mocked_response


@pytest.fixture
def mock_db():
    """Create a properly configured mock db session for unit tests."""
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()

    # Configure execute to return proper results for count queries
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0  # Default count = 0
    mock_result.scalars.return_value = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result

    # Configure scalar for direct scalar() calls (used by services)
    db.scalar = AsyncMock(return_value=None)

    # Configure common async methods
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    # For session context manager
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=None)

    return db


class MockUser:
    """Mock user object that supports both attribute and dict-like access."""

    # Default tenant UUID for tests
    DEFAULT_TENANT_ID = uuid7()

    def __init__(
        self,
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=None,  # Default to None, will be set to DEFAULT_TENANT_ID
        roles=None,
        permissions=None,
    ):
        self.id = id
        self.username = username
        self.email = email
        self.is_superuser = is_superuser
        self.is_active = is_active
        self.tenant_id = tenant_id if tenant_id is not None else self.DEFAULT_TENANT_ID
        self.roles = roles or []
        # Default permissions for superuser - all common permissions
        self.permissions = (
            permissions
            if permissions is not None
            else [
                "MANAGE_STORAGE_SETTINGS",
                "MANAGE_SSO_SETTINGS",
                "MANAGE_WEBHOOKS",
                "MANAGE_USERS",
                "MANAGE_ROLES",
                "MANAGE_TENANTS",
                "MANAGE_BILLING",
                "MANAGE_API_KEYS",
                "VIEW_AUDIT_LOGS",
            ]
            if is_superuser
            else []
        )

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return self.is_superuser or permission in self.permissions


def create_mock_engine():
    """Create a mock SQLAlchemy engine that ignores RLS commands."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_engine = Mock()
    mock_engine.connect.return_value = mock_cm
    return mock_engine


@pytest.fixture
async def full_mock_context(get_test_app):
    """Full mock context for async integration tests.

    Sets up common mocks: tenant, rate limiting, cache warming, db override, auth.
    """
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.api.dependencies import get_current_user
    from src.app.core.db.database import async_get_db

    # Mock superuser with dict-like access for routes using current_user["id"]
    fake_superuser = MockUser(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=MockUser.DEFAULT_TENANT_ID,  # Use UUID
    )

    async def mock_get_current_user():
        return fake_superuser

    # Mock for _get_current_user_from_token used by RBAC dependencies
    async def mock_get_current_user_from_token(token: str = None, db=None):
        return fake_superuser

    # Note: We use dependency_overrides for async_get_db because FastAPI
    # needs to properly handle the async generator in dependency injection
    from src.app.core.db.database import async_get_db

    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.middleware.rate_limit.settings.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA rate limit check
        patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token", side_effect=mock_get_current_user_from_token
        ),
        patch("src.app.middleware.tenant.async_engine", new=create_mock_engine()),
    ):
        yield

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


@pytest.fixture
async def unauthenticated_mock_context(get_test_app):
    """Mock context WITHOUT authentication for testing auth-required endpoints.

    Sets up common mocks: tenant, rate limiting, cache warming, db override.
    Does NOT mock get_current_user - allows real auth dependency to run.
    """
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.core.db.database import async_get_db

    # Note: We use dependency_overrides for async_get_db instead of patch
    # because override_get_db is an async context manager

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.middleware.rate_limit.settings.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA rate limit check
        patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch("src.app.middleware.tenant.async_engine", new=create_mock_engine()),
    ):
        yield

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async database session fixture."""
    async with get_testing_session_local()() as session:
        yield session


@pytest.fixture
async def async_client(get_test_app) -> AsyncGenerator[AsyncClient, None]:
    """Async client fixture for all tests."""
    app = get_test_app
    
    from src.app.core.db.database import async_get_db
    app.dependency_overrides[async_get_db] = override_get_db
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"User-Agent": "TestClient/1.0", "X-Tenant-ID": "test-tenant-e2e"},
    ) as ac:
        yield ac
        
    app.dependency_overrides.clear()


@pytest.fixture
async def unauthenticated_async_client(get_test_app, unauthenticated_mock_context) -> AsyncGenerator[AsyncClient, None]:
    """Async client WITHOUT authentication for testing auth-required endpoints.

    Use this fixture when testing that endpoints correctly reject unauthenticated requests.
    """
    app = get_test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-integration"},
    ) as ac:
        yield ac


@pytest.fixture
async def superuser_token_headers(get_test_app, full_mock_context):
    """Headers for superuser requests (overrides get_current_user)."""
    app = get_test_app
    from src.app.api.dependencies import get_current_user

    # Create superuser mock
    fake_superuser = Mock()
    fake_superuser.id = 1
    fake_superuser.is_superuser = True
    fake_superuser.username = "admin"
    fake_superuser.email = "admin@example.com"
    fake_superuser.is_active = True
    fake_superuser.__getitem__ = lambda self, key: getattr(self, key)
    fake_superuser.get = lambda key, default=None: getattr(fake_superuser, key, default)

    # Override dependency (updates existing overrides from full_mock_context)
    app.dependency_overrides[get_current_user] = lambda: fake_superuser
    return {
        "Authorization": "Bearer superuser",
        "X-Tenant-ID": "test-tenant-integration",
    }


@pytest.fixture
async def regular_user_token_headers(get_test_app, full_mock_context):
    """Headers for regular user requests (non-superuser)."""
    app = get_test_app
    from src.app.api.dependencies import get_current_user

    # Create regular user (NOT superuser)
    fake_regular_user = MockUser(
        id=2,
        username="regular_user",
        email="user@example.com",
        is_superuser=False,
        is_active=True,
        tenant_id=MockUser.DEFAULT_TENANT_ID,  # Use UUID
    )

    # Override dependency to return regular user
    app.dependency_overrides[get_current_user] = lambda: fake_regular_user
    return {
        "Authorization": "Bearer regular",
        "X-Tenant-ID": "test-tenant-integration",
    }


@pytest.fixture
async def auth_headers(tenant, superuser_token_headers):
    """Authentication headers for tenant context with valid auth token."""
    return {
        **superuser_token_headers,
        "X-Tenant-ID": tenant.slug,
    }


class FakeRedisPipeline:
    """Fake Redis Pipeline for batched operations."""

    def __init__(self, redis):
        self.redis = redis
        self._commands = []
        self._results = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            return self

        return method

    def zscore(self, key, member):
        """Queue a zscore command."""
        self._commands.append(("zscore", key, member))
        return self

    def zadd(self, key, mapping, nx=False, xx=False, gt=False, lt=False):
        """Queue a zadd command."""
        self._commands.append(("zadd", key, mapping))
        return self

    def expire(self, key, seconds):
        """Queue an expire command."""
        self._commands.append(("expire", key, seconds))
        return self

    def get(self, key):
        """Queue a get command."""
        self._commands.append(("get", key))
        return self

    def set(self, key, value, ex=None, px=None, **kwargs):
        """Queue a set command."""
        self._commands.append(("set", key, value, ex))
        return self

    def hincrby(self, key, field, amount):
        """Queue a hincrby command."""
        self._commands.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key, field, amount):
        """Queue a hincrbyfloat command."""
        self._commands.append(("hincrbyfloat", key, field, amount))
        return self

    def lpush(self, key, value):
        """Queue a lpush command."""
        self._commands.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, end):
        """Queue a ltrim command."""
        self._commands.append(("ltrim", key, start, end))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        pass

    async def execute(self):
        """Execute all queued commands and return results."""
        results = []
        for cmd in self._commands:
            if cmd[0] == "zscore":
                result = await self.redis.zscore(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "zadd":
                result = await self.redis.zadd(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "expire":
                result = await self.redis.expire(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "get":
                result = await self.redis.get(cmd[1])
                results.append(result)
            elif cmd[0] == "set":
                result = await self.redis.set(cmd[1], cmd[2], ex=cmd[3] if len(cmd) > 3 else None)
                results.append(result)
            elif cmd[0] == "hincrby":
                result = await self.redis.hincrby(cmd[1], cmd[2], cmd[3])
                results.append(result)
            elif cmd[0] == "hincrbyfloat":
                result = await self.redis.hincrbyfloat(cmd[1], cmd[2], cmd[3])
                results.append(result)
            elif cmd[0] == "lpush":
                result = await self.redis.lpush(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "ltrim":
                result = await self.redis.ltrim(cmd[1], cmd[2], cmd[3])
                results.append(result)
            else:
                results.append(None)
        self._commands = []
        return results


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.connection_kwargs = {"protocol": 3}

    async def get(self, key):
        if not key:
            return None
        return self.store.get(key)

    async def exists(self, key):
        """Check if a key exists in the store."""
        return 1 if key in self.store else 0

    async def set(self, key, value, ex=None, px=None, **kwargs):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        count = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                count += 1
        return count

    def _get_key(self, key):
        if isinstance(key, bytes):
            return key.decode("utf-8")
        return key

    async def hgetall(self, key):
        key = self._get_key(key)
        if key not in self.store:
            return {}
        val = self.store.get(key)
        if not isinstance(val, dict):
            return {}
        # Return bytes keys/values as redis-py does
        return {
            k.encode("utf-8") if isinstance(k, str) else k: str(v).encode("utf-8") if not isinstance(v, bytes) else v
            for k, v in val.items()
        }

    async def hset(self, key, mapping):
        key = self._get_key(key)
        if key not in self.store:
            self.store[key] = {}
        if not isinstance(self.store[key], dict):
            self.store[key] = {}

        for field, value in mapping.items():
            self.store[key][field] = value
        return len(mapping)

    async def hincrby(self, key, field, amount):
        key = self._get_key(key)
        if key not in self.store:
            self.store[key] = {}
        if not isinstance(self.store[key], dict):
            self.store[key] = {}

        current = int(self.store[key].get(field, 0))
        new_val = current + amount
        self.store[key][field] = str(new_val)
        return new_val

    async def hincrbyfloat(self, key, field, amount):
        key = self._get_key(key)
        if key not in self.store:
            self.store[key] = {}
        if not isinstance(self.store[key], dict):
            self.store[key] = {}

        current = float(self.store[key].get(field, 0.0))
        new_val = current + amount
        self.store[key][field] = str(new_val)
        return new_val

    async def lpush(self, key, value):
        key = self._get_key(key)
        if key not in self.store:
            self.store[key] = []
        if not isinstance(self.store[key], list):
            self.store[key] = []

        self.store[key].insert(0, value)
        return len(self.store[key])

    async def lrange(self, key, start, end):
        key = self._get_key(key)
        if key not in self.store:
            return []
        lst = self.store.get(key)
        if not isinstance(lst, list):
            return []

        if end == -1:
            slice_end = len(lst)
        else:
            slice_end = end + 1

        result = lst[start:slice_end]
        return [str(x).encode("utf-8") if not isinstance(x, bytes) else x for x in result]

    async def ltrim(self, key, start, end):
        key = self._get_key(key)
        if key in self.store:
            lst = self.store[key]
            if isinstance(lst, list):
                if end == -1:
                    self.store[key] = lst[start:]
                else:
                    self.store[key] = lst[start : end + 1]
        return True

    async def llen(self, key):
        key = self._get_key(key)
        lst = self.store.get(key)
        if isinstance(lst, list):
            return len(lst)
        return 0

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def setex(self, key, seconds, value):
        """Set key with expiration (seconds ignored in mock)."""
        self.store[key] = value
        return True

    async def expire(self, key, seconds):
        return bool(key in self.store)

    async def ttl(self, key):
        return 60 if key in self.store else -2

    async def pttl(self, key):
        return 60000 if key in self.store else -2

    async def scan(self, cursor=0, match=None, count=None):
        import fnmatch

        if cursor != 0:
            return 0, []
        keys = list(self.store.keys())
        if match:
            keys = fnmatch.filter(keys, match)
        return 0, keys

    async def scan_iter(self, match=None, count=None, _type=None):
        """Simulate scan_iter."""
        import fnmatch

        keys = list(self.store.keys())
        if match:
            keys = fnmatch.filter(keys, match)
        for key in keys:
            yield key

    async def ping(self):
        """Simulate Redis ping for health checks."""
        return True

    async def close(self):
        pass

    async def aclose(self):
        """Simulate async connection closing."""
        await self.close()

    async def enqueue_job(self, function, *args, **kwargs):
        return Mock(job_id="job_mock_123")

    async def script_load(self, script: str):
        return "mock_script_sha"

    async def evalsha(self, sha, numkeys, *keys_and_args):
        """Simulate Lua scripts based on context.

        Handles both rate limit and token blacklist Lua scripts:
        - Rate limit: returns incremented counter
        - Token blacklist: returns 0 (not blacklisted) or 1 (blacklisted)
        """
        # For token blacklist script: KEYS[1]=negative_key, KEYS[2]=blacklist_key
        # ARGV[1]=token, ARGV[2]=now, ARGV[3]=ttl
        if numkeys == 2 and len(keys_and_args) >= 5:
            # Token blacklist script
            negative_key = keys_and_args[0]
            blacklist_key = keys_and_args[1]
            token = keys_and_args[2]

            # Check negative cache
            if negative_key in self.store:
                return 0  # Not blacklisted (cached)

            # Check blacklist sorted set
            zset = self.store.get(blacklist_key, {})
            if not isinstance(zset, dict):
                zset = {}
            score = zset.get(token)

            if score is None:
                # Not in blacklist, cache negative result
                self.store[negative_key] = "1"
                return 0  # Not blacklisted

            # Check if expired (score is timestamp)
            now = float(keys_and_args[3]) if len(keys_and_args) > 3 else 0
            if score < now:
                # Expired, remove
                if token in zset:
                    del zset[token]
                self.store[negative_key] = "1"
                return 0  # Not blacklisted (expired)

            return 1  # IS blacklisted

        # Default: Rate limit script (single key)
        key = keys_and_args[0] if keys_and_args else "default_key"
        current = self.store.get(key, 0)
        if isinstance(current, str):
            current = int(current)
        current += 1
        self.store[key] = current
        return current

    async def eval(self, script: str, numkeys: int, *keys_and_args):
        """Execute a Lua script (mock implementation).

        Handles common Redis Lua patterns:
        - GETDEL: Get and delete a key atomically
        - Rate limit scripts: Increment counter
        - Token blacklist scripts: Check blacklist status
        """
        # Detect GETDEL pattern: "local value = redis.call('GET', KEYS[1])"
        if "GET" in script and "DEL" in script and numkeys >= 1:
            key = keys_and_args[0]
            value = self.store.get(key)
            if key in self.store:
                del self.store[key]
            return value

        # Delegate to evalsha for other patterns
        return await self.evalsha("mock_script_sha", numkeys, *keys_and_args)

    async def zscore(self, key, member):
        """Get the score of a member in a sorted set.

        Returns None if the key doesn't exist or member is not in the set.
        This is used by token_blacklist_cache.is_token_blacklisted.
        """
        zset = self.store.get(key)
        if zset is None or not isinstance(zset, dict):
            return None
        return zset.get(member)

    async def zadd(self, key, mapping, nx=False, xx=False, gt=False, lt=False):
        """Add members to a sorted set with scores."""
        if key not in self.store:
            self.store[key] = {}
        zset = self.store[key]
        if not isinstance(zset, dict):
            self.store[key] = {}
            zset = self.store[key]
        count = 0
        for member, score in mapping.items():
            if member not in zset:
                count += 1
            zset[member] = score
        return count

    async def zremrangebyscore(self, key, min_score, max_score):
        """Remove members from a sorted set by score range."""
        if key not in self.store:
            return 0
        zset = self.store.get(key, {})
        if not isinstance(zset, dict):
            return 0
        to_remove = [m for m, s in zset.items() if min_score <= s <= max_score]
        for m in to_remove:
            del zset[m]
        return len(to_remove)

    def pipeline(self, transaction=True):
        """Return a pipeline mock for batched operations."""
        return FakeRedisPipeline(self)


@pytest.fixture(autouse=True)
def mock_event_bus(monkeypatch):
    """Mock event bus to prevent Redis connections in tests."""
    from unittest.mock import AsyncMock, MagicMock
    from src.app.core.events import EventBus, EventPriority

    # Create a mock event bus that does nothing
    mock_bus = MagicMock(spec=EventBus)
    mock_bus.publish = AsyncMock()
    mock_bus.publish_sync = MagicMock()
    mock_bus.start = AsyncMock()
    mock_bus.stop = AsyncMock()
    mock_bus._handlers = {}  # Required for handler registry tests

    # Patch the event_bus singleton
    monkeypatch.setattr("src.app.core.events.event_bus", mock_bus)

    yield mock_bus


@pytest.fixture(autouse=True)
def mock_redis(request):
    """Mock Redis client with in-memory state.

    Automatically applies to all tests EXCEPT those marked with:
    - @pytest.mark.integration
    - @pytest.mark.e2e
    - @pytest.mark.uses_real_redis

    These markers indicate tests that need real Redis connections.
    """
    # Skip mocking for tests that need real Redis
    markers = [m.name for m in request.node.iter_markers()]
    if any(m in markers for m in ("integration", "e2e", "uses_real_redis")):
        # Real Redis initialization is handled by init_real_redis_async fixture
        yield None
        return

    fake_redis = FakeRedis()

    # Ensure setup module is imported for patching
    import os

    from src.app.core import setup
    from src.app.core import redis_factory

    # Force attribute to exist for pkgutil.resolve_name used by mock.patch
    if not hasattr(setup, "create_redis_cache_pool"):
        setup.create_redis_cache_pool = setup.create_redis_cache_pool

    with (
        patch("src.app.core.setup.create_redis_cache_pool", new=AsyncMock()),
        patch("src.app.core.utils.cache.client", new=fake_redis),
        # Patch all top-level importers of redis_client to avoid NoneType errors
        # patch("src.app.modules.security.service.redis_client", new=fake_redis), # REMOVED: Refactored to use cache.client
        patch("src.app.modules.feature_flags.service.redis_client", new=fake_redis),
        patch("src.app.modules.mfa.router.redis_client", new=fake_redis),
        patch("src.app.core.security.auto_ban.redis_client", new=fake_redis),
        patch("src.app.core.security.rate_limit_cache.redis_client", new=fake_redis),
        # REMOVED: token_blacklist_cache refactored to use cache.client dynamically
        # patch("src.app.core.security.token_blacklist_cache.redis_client", new=fake_redis),
        patch("src.app.modules.sso_settings.service.redis_client", new=fake_redis),
        patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=fake_redis),
        patch("src.app.core.utils.queue.pool", new=fake_redis),
        patch("src.app.core.redis_factory.get_redis_pool", new=AsyncMock(return_value=fake_redis)),  # Add this
        patch("src.app.core.redis_factory.close_redis_pool", new=AsyncMock()),  # Add this
    ):
        yield fake_redis


@pytest_asyncio.fixture(autouse=True)
async def init_real_redis_async(request):
    """Initialize real Redis client for tests that need it.

    This fixture runs BEFORE tests marked with:
    - @pytest.mark.integration
    - @pytest.mark.e2e
    - @pytest.mark.uses_real_redis

    It initializes cache.client with a real Redis connection.
    """
    markers = [m.name for m in request.node.iter_markers()]
    needs_real_redis = any(m in markers for m in ("integration", "e2e", "uses_real_redis"))

    if not needs_real_redis:
        yield
        return

    # Initialize real Redis client
    from src.app.core.utils import cache
    from src.app.core import redis_factory
    from src.app.core.utils.gcra_rate_limit import gcra_rate_limiter as gcra_global, GCRARateLimiter
    import redis.asyncio as redis

    # CRITICAL: Reset ALL global state to prevent "Event loop is closed" errors
    # The redis_factory caches the pool globally, and that pool gets bound to
    # a specific event loop. When that loop closes, the pool becomes unusable.
    # We must reset BOTH cache.client/pool AND redis_factory._redis_pool
    # Also reset GCRA rate limiter which caches script SHAs per connection
    cache.client = None
    cache.pool = None
    redis_factory._redis_pool = None

    # Reset GCRA rate limiter to force fresh initialization
    # The script SHA is bound to a specific Redis connection
    import src.app.core.utils.gcra_rate_limit as gcra_module

    gcra_module.gcra_rate_limiter = None

    try:
        # Create NEW connection pool for THIS event loop
        pool = await redis_factory.get_redis_pool()
        cache.pool = pool
        cache.client = redis.Redis(connection_pool=pool, decode_responses=True)

        # Initialize GCRA rate limiter with new client
        gcra_module.gcra_rate_limiter = GCRARateLimiter(cache.client)

        # Flush before test for isolation
        await cache.client.flushdb()

        # Clear L1 caches (in-memory caches that persist across tests)
        from src.app.middleware.tenant import _L1_TENANT_CACHE
        from src.app.modules.rbac.service import _L1_RBAC_CACHE

        _L1_TENANT_CACHE.clear()
        _L1_RBAC_CACHE.clear()
    except Exception as e:
        print(f"Warning: Failed to initialize real Redis: {e}")
        raise

    yield

    # Cleanup after test - just reset globals, don't try to close
    # (the event loop may be closing and we can't operate on closed loops)
    cache.client = None
    cache.pool = None
    redis_factory._redis_pool = None
    gcra_module.gcra_rate_limiter = None


@pytest.fixture(autouse=True)
async def cleanup_real_redis(request):
    """Clean up real Redis after integration/e2e tests.

    This fixture runs AFTER tests that use real Redis connections
    (marked with @pytest.mark.integration, @pytest.mark.e2e, or @pytest.mark.uses_real_redis).

    Cleans rate limit keys, auth cache, tenant cache, and token blacklist
    to prevent test isolation issues.
    """
    # Check markers FIRST - skip if not a real Redis test
    markers = [m.name for m in request.node.iter_markers()]
    needs_real_redis = any(m in markers for m in ("integration", "e2e", "uses_real_redis"))

    if not needs_real_redis:
        yield
        return

    # SETUP: Clear L1 caches BEFORE test (in-memory caches that persist across tests)
    try:
        from src.app.middleware.tenant import _L1_TENANT_CACHE

        _L1_TENANT_CACHE.clear()
    except ImportError:
        pass
    try:
        from src.app.modules.rbac.service import _L1_RBAC_CACHE

        _L1_RBAC_CACHE.clear()
    except ImportError:
        pass

    yield  # Test runs here - SINGLE YIELD

    # TEARDOWN: Clean Redis AFTER test
    from src.app.core.utils import cache

    if not cache.client:
        return

    import logging

    logger = logging.getLogger(__name__)

    # Patterns to clean - covers all rate limit and auth keys
    patterns_to_clean = [
        "ratelimit:*",  # Fixed window rate limit keys
        "gcra:*",  # GCRA rate limit keys
        "user:auth:*",  # Auth cache
        "tenant:*",  # Tenant cache
        "blacklist:*",  # Token blacklist
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
        except Exception as e:
            logger.warning(f"Failed to clean Redis pattern {pattern}: {e}")

    if total_deleted:
        logger.info(f"cleanup_real_redis: Deleted {total_deleted} keys")

    # Clear tenant context to prevent leakage between tests
    try:
        from src.app.core.tenancy.context import clear_tenant_context

        clear_tenant_context()
    except Exception:
        pass


@pytest.fixture
def mock_user(current_user_dict):
    """Fixture to mock current user dependency."""
    return current_user_dict


@pytest.fixture
async def create_test_user(db_session, tenant):
    """Factory fixture to create a test user."""
    from src.app.core.security import get_password_hash

    async def _create_user(email: str = "test@example.com", is_superuser: bool = False, **kwargs):
        kwargs.pop("is_active", None)
        hashed_password = await get_password_hash("password")
        user = User(
            email=email,
            username=email.split("@")[0],
            hashed_password=hashed_password,
            is_superuser=is_superuser,
            tenant_id=tenant.id,
            name="Test User",
            **kwargs,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _create_user


@pytest.fixture
def sample_user_data():
    """Generate sample user data for tests."""
    return {
        "name": fake.name(),
        "username": fake.user_name(),
        "email": fake.email(),
        "password": fake.password(),
    }


@pytest.fixture
def sample_user_read():
    """Generate a sample UserRead object."""
    from src.app.schemas.user import UserRead

    return UserRead(
        id=1,
        uuid=uuid7(),
        name=fake.name(),
        username=fake.user_name(),
        email=fake.email(),
        profile_image_url=fake.image_url(),
        is_superuser=False,
        created_at=fake.date_time(),
        updated_at=fake.date_time(),
        tier_id=None,
        tenant_id=MockUser.DEFAULT_TENANT_ID,  # Add required tenant_id field
    )


@pytest.fixture
def current_user_dict():
    """Mock current user from auth dependency."""
    return {
        "id": 1,
        "username": fake.user_name(),
        "email": fake.email(),
        "name": fake.name(),
        "is_superuser": False,
    }


@pytest.fixture
def mock_sso_settings():
    """Fixture to mock SSO settings for Apple provider."""

    patches = []

    def _mock_sso_settings(provider: str, enabled: bool = True):
        patcher = patch(
            "src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings",
            new_callable=AsyncMock,
        )
        mock = patcher.start()
        patches.append(patcher)

        mock.return_value = {
            "client_id": "com.example.app",
            "client_secret": "test-secret",
            "team_id": "TEAMID1234",
            "key_id": "KEYID12345",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
            "is_enabled": enabled,
        }
        return mock

    yield _mock_sso_settings

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def mock_apple_oauth():
    """Fixture to mock Apple OAuth responses."""

    patches = []

    def _mock_apple_oauth(token_response: dict = None):
        # We mock the provider instance returned by _get_provider_instance
        # This is safer than patching methods on the class

        mock_provider = AsyncMock()
        mock_provider.exchange_code_for_token.return_value = token_response or {
            "access_token": "mock-access-token",
            "id_token": "mock-id-token",
            "refresh_token": "mock-refresh-token",
            "expires_in": 3600,
            "token_type": "bearer",
        }

        # Default user info setup
        # We can't import AppleUserInfo here easily if it depends on other things,
        # so we mock the return value object
        mock_user_info = Mock()
        mock_user_info.provider_user_id = "test.apple.id"
        mock_user_info.email = "test@example.com"
        mock_user_info.email_verified = True
        mock_user_info.name = "Test User"
        mock_user_info.is_private_email = False

        mock_provider.get_user_info.return_value = mock_user_info

        # Patch the factory method in service
        patcher = patch("src.app.modules.sso.service.SSOService._get_provider_instance", return_value=mock_provider)
        patcher.start()
        patches.append(patcher)

        return mock_provider

    yield _mock_apple_oauth

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def mock_encryption():
    """Mock encryption functions to avoid key/bytes issues."""
    with (
        patch("src.app.modules.webhooks.crypto.encrypt_data") as mock_encrypt,
        patch("src.app.modules.webhooks.crypto.decrypt_data") as mock_decrypt,
    ):
        mock_encrypt.return_value = b"encrypted_data"
        mock_decrypt.return_value = b"decrypted_data"
        yield mock_encrypt, mock_decrypt


@pytest.fixture
def tenant(db):
    """Create and return a sample tenant."""
    # Check if tenant already exists to avoid UniqueViolation
    existing = db.query(Tenant).filter(Tenant.slug == "test-tenant-integration").first()
    if existing:
        return existing

    tenant_data = {
        "name": "Test Tenant Integration",
        "slug": "test-tenant-integration",
        "is_active": True,
    }
    tenant = Tenant(**tenant_data)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


# -----------------------------------------------------------------------------
# Real PostgreSQL Fixtures (for integration/e2e tests)
# Moved from tests/integration/conftest.py to be available to all test types
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def real_postgres_engine():
    """Create real PostgreSQL engine for integration/e2e tests.

    Uses function scope to prevent event loop binding issues.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    # Build full async URL with scheme prefix
    # Use TEST_POSTGRES_PORT env var if set, otherwise use main POSTGRES_PORT
    test_port = os.getenv("TEST_POSTGRES_PORT", str(settings.POSTGRES_PORT))
    test_db = os.getenv("TEST_POSTGRES_DB", "test_db")

    base_uri = str(settings.POSTGRES_URI)
    # Replace port if TEST_POSTGRES_PORT is different
    if test_port != str(settings.POSTGRES_PORT):
        base_uri = base_uri.replace(f":{settings.POSTGRES_PORT}", f":{test_port}")
    # Replace database name with test database
    base_uri = base_uri.replace(f"/{settings.POSTGRES_DB}", f"/{test_db}")

    # Add async scheme prefix
    test_db_url = f"{settings.POSTGRES_ASYNC_PREFIX}{base_uri}"

    engine = create_async_engine(
        test_db_url,
        echo=True,  # Log all SQL
        poolclass=NullPool,  # Critical: prevents connection caching across event loops
    )

    yield engine

    # Handle event loop closed during teardown
    try:
        await engine.dispose()
    except RuntimeError:
        # Event loop may be closed during test session teardown
        pass


@pytest_asyncio.fixture
async def real_postgres(real_postgres_engine) -> AsyncGenerator[AsyncSession, None]:
    """Real PostgreSQL session for integration/e2e tests.

    Uses function scope (default) to ensure each test gets a fresh session,
    preventing 'another operation is in progress' errors from shared connections.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    async_session = sessionmaker(
        real_postgres_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session
        # Session context manager handles cleanup automatically


# -----------------------------------------------------------------------------
# Real Redis Fixtures (for integration/e2e tests)
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def real_redis() -> AsyncGenerator[Any, None]:
    """Real Redis connection for integration/e2e tests.

    Uses function scope (default) to create fresh connections per test,
    preventing 'Future attached to different loop' errors.
    """
    from redis.asyncio import Redis

    # Use TEST_REDIS_PORT env var if set, otherwise use main REDIS_CACHE_PORT
    test_port = int(os.getenv("TEST_REDIS_PORT", str(settings.REDIS_CACHE_PORT)))
    test_password = os.getenv("TEST_REDIS_PASSWORD", settings.REDIS_CACHE_PASSWORD)

    redis = Redis(
        host=settings.REDIS_CACHE_HOST,
        port=test_port,
        password=test_password if test_password else None,
        db=0,
        decode_responses=True,
    )

    # Flush before test for isolation
    try:
        await redis.flushdb()
    except Exception:
        pass

    yield redis

    # Cleanup after test
    try:
        await redis.flushdb()
        await redis.aclose()
    except Exception:
        pass


@pytest_asyncio.fixture
async def redis_client(real_redis):
    """Alias for real_redis for backward compatibility.

    Many tests use 'redis_client' as the fixture name, while others use 'real_redis'.
    This alias ensures both work identically.
    """
    return real_redis


# -----------------------------------------------------------------------------
# Factory Fixtures (for integration/e2e tests)
# These use real_postgres to create test entities in the database
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def test_tier(real_postgres):
    """Create a test tier in real PostgreSQL."""
    from tests.factories import TierFactory

    return await TierFactory.create_async(real_postgres, name="TestTier")


@pytest_asyncio.fixture
async def test_tenant(real_postgres):
    """Create a test tenant in real PostgreSQL.

    Returns the tenant object directly for backward compatibility.
    Tests can access test_tenant.id, test_tenant.slug, etc.
    """
    from tests.factories import TenantFactory

    return await TenantFactory.create_async(real_postgres)


@pytest_asyncio.fixture
async def other_tenant(real_postgres):
    """Create a second test tenant for multi-tenant security tests.

    This fixture is used by tests that verify tenant isolation
    (e.g., webhook security tests that ensure users can't access
    other tenants' resources).

    Returns:
        Tenant: A different tenant than test_tenant
    """
    from tests.factories import TenantFactory

    return await TenantFactory.create_async(
        real_postgres,
        name="Other Tenant",
        slug="other-tenant",
    )


@pytest_asyncio.fixture
async def other_user(real_postgres, other_tenant):
    """Create a test user for the other tenant.

    Returns a dict with access_token for authenticated requests.
    """
    from tests.factories import UserFactory
    from src.app.core.security import create_access_token, create_refresh_token

    user = await UserFactory.create_async(
        real_postgres,
        tenant_id=other_tenant.id,
        email="other@test.com",
        username="other_user",
    )

    access_token = await create_access_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(other_tenant.id)}
    )
    refresh_token = await create_refresh_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(other_tenant.id)}
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user,
        "tenant": other_tenant,
    }


@pytest_asyncio.fixture
async def other_auth_headers(other_user):
    """Authentication headers for the other tenant.

    Used for multi-tenant security tests to verify tenant isolation.
    """
    return {
        "Authorization": f"Bearer {other_user['access_token']}",
        "X-Tenant-ID": other_user["tenant"].slug,
    }


@pytest_asyncio.fixture
async def test_user(real_postgres, test_tenant):
    """Create a test user with associated tenant in real PostgreSQL.

    This fixture returns a dict with 'access_token' for authenticated requests
    and the user object for direct access.

    Returns:
        dict: {"access_token": str, "user": User, "refresh_token": str, "tenant": Tenant}
    """
    from tests.factories import UserFactory
    from src.app.core.security import create_access_token, create_refresh_token

    # Create test user
    user = await UserFactory.create_async(real_postgres, tenant_id=test_tenant.id)

    # Generate tokens for authentication (async functions)
    access_token = await create_access_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(test_tenant.id)}
    )
    refresh_token = await create_refresh_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(test_tenant.id)}
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user,
        "tenant": test_tenant,
    }


@pytest_asyncio.fixture
async def admin_user(real_postgres, test_tenant):
    """Create an admin user with superuser privileges in real PostgreSQL.

    This fixture returns a dict with 'access_token' for authenticated requests
    and the user object for direct access.

    Returns:
        dict: {"access_token": str, "user": User, "refresh_token": str, "tenant": Tenant}
    """
    from tests.factories import UserFactory
    from src.app.core.security import create_access_token, create_refresh_token

    # Create admin user with superuser privileges
    user = await UserFactory.create_async(
        real_postgres,
        tenant_id=test_tenant.id,
        is_superuser=True,
        email="admin@test.com",
        username="admin_test",
    )

    # Generate tokens (async functions)
    access_token = await create_access_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(test_tenant.id)}
    )
    refresh_token = await create_refresh_token(
        data={"sub": str(user.uuid), "email": user.email, "tenant_id": str(test_tenant.id)}
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user,
        "tenant": test_tenant,
    }


@pytest.fixture
async def e2e_client() -> AsyncGenerator[AsyncClient, None]:
    """E2E client fixture with real database and Redis connections.

    This fixture is used by E2E tests that need actual infrastructure.
    It uses the real app with actual dependencies (not mocked).
    """
    from src.app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-e2e"},
    ) as ac:
        yield ac


@pytest.fixture
def override_settings():
    """Fixture to temporarily override settings for tests.

    Returns a context manager that allows temporary setting overrides.
    """
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def _override(**kwargs):
        patches = []
        for key, value in kwargs.items():
            if hasattr(settings, key):
                patcher = patch.object(settings, key, value)
                patcher.start()
                patches.append(patcher)
        try:
            yield
        finally:
            for patcher in patches:
                patcher.stop()

    return _override
