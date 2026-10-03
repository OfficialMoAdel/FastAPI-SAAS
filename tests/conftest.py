import base64
import os
import sys
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

# Force UTF-8 encoding for standard output/error to prevent UnicodeEncodeError crashes on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


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

# NOTE: worker task batches and collaborators are registered by src.app.worker.
# Do NOT import it here. A module-level import in conftest runs before the
# event loop each integration test builds, and it broke
# tests/integration/modules/ai with "RuntimeError: Event loop is closed".
# Tests that invoke a worker task directly wire the seam in their own module,
# where the import order is theirs to control.

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef12")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdmin123!@#456")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "test_db")
os.environ.setdefault("POSTGRES_SERVER", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("REDIS_QUEUE_HOST", "localhost")
os.environ.setdefault("REDIS_QUEUE_PORT", "6379")
os.environ.setdefault("REDIS_CACHE_HOST", "localhost")
os.environ.setdefault("REDIS_CACHE_PORT", "6379")

# C-1 note: setdefault (not force-set) is deliberate — DB-backed tests
# inherit the developer's real credentials from src/.env (loaded by the root
# conftest with override=True before this file runs). Forcing the DSN here
# breaks that pairing (verified by A/B: forced triplet → 53 failures).
# A shell-exported POSTGRES_PASSWORD matching a leaked value no longer
# aborts collection: the validator warns (local) instead of raising, and
# hard-fails only in staging/production.

# Load environment variables from src/.env — override=False only fills keys
# that are still unset, preserving the root-conftest/critical values above.
load_dotenv("src/.env", override=False)

# Disable bad bot filter for tests (httpx is used by test clients)
os.environ["BAD_BOT_FILTER_ENABLED"] = "false"
# P0-1 fixed: attack detection no longer consumes request bodies
# (reads via Request.body(), replayed downstream by Starlette), so the
# middleware stays enabled in tests. ATTACK_DETECTION_MODE defaults to
# "block" — keep test payloads free of attack patterns or override per-test.
# Enable test mode to use NullPool and prevent event loop closed errors
os.environ["TESTING"] = "1"

from src.app.core.config import RedisCacheSettings, settings

# Monkey-patch REDIS_CACHE_URL to use isolated DB indices per worker
# This prevents Redis key collisions when multiple xdist workers share the same Redis instance
original_redis_url = RedisCacheSettings.REDIS_CACHE_URL.fget


def _get_isolated_redis_url(self):
    base_url = original_redis_url(self)
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    db_index = int(worker_id.replace("gw", "")) if worker_id.startswith("gw") else 0
    # Ensure base_url doesn't already have a path
    if base_url.count("/") > 2:
        return base_url
    return f"{base_url}/{db_index}"


RedisCacheSettings.REDIS_CACHE_URL = property(_get_isolated_redis_url)

# Wire user-domain JWT security lookups for the test session.
# Production wires these in the lifespan (core/setup.py); unit tests never
# run lifespan, so register explicitly here instead of at module import time.
from src.app.modules.users import register_users_security_lookups as _register_users_security_lookups

_register_users_security_lookups()


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
settings.crypt.ENCRYPTION_KEY = SecretStr(_test_encryption_key)

_test_encryption_salt = os.getenv("TEST_ENCRYPTION_SALT")
if not _test_encryption_salt:
    _test_encryption_salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
settings.crypt.ENCRYPTION_SALT = SecretStr(_test_encryption_salt)

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


# A-M6: single model-registration point — imports every model for its
# Base.metadata side-effect (billing/feature_flags/notifications included,
# which the old tenants-loop provided implicitly). Replaces the 30-line
# manual import block that rotted out of sync with models/__init__.py.
from src.app import models  # noqa: F401
from src.app.core.db.database import Base
from src.app.models import Tenant, User

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

    # Clear any existing dependency overrides from previous tests.
    # Test principal injection lives in tests/helpers/auth.py
    # (override_test_principal / install_test_principal). Production
    # `get_auth_context` runs unmodified so HTTP tests cross the real seam.
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

    # reset_registry() restores the declared default set itself, so there is no
    # hand-registration here any more: the registry owns which breakers exist,
    # not this fixture.
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

    The jwt_service DIP seams (``_token_version_lookup`` /
    ``_user_tenant_lookup``) are deliberately NOT reset here. They are wired
    once at import of ``src.app.modules.users`` and every test that needs a
    lookup reads them; nulling them per-test makes the lookup-aware tests
    fail for every test, which is a worse failure than the occasional leak it
    was meant to prevent. Tests that register their own lookup reset it
    themselves via ``reset_jwt_security_lookups()``.
    """
    # Import reset functions - these are added for test isolation
    from src.app.core.events import reset_event_bus
    from src.app.core.security.jwt_utils import clear_decode_failure_cache, clear_jwt_key_cache
    from src.app.core.security.token_blacklist_cache import reset_token_blacklist_cache
    from src.app.core.utils.gcra_rate_limit import reset_gcra_rate_limiter
    from src.app.core.utils.rate_limit import reset_rate_limiter

    # Pre-test reset
    reset_rate_limiter()
    reset_gcra_rate_limiter()
    reset_token_blacklist_cache()
    reset_event_bus()
    clear_jwt_key_cache()
    clear_decode_failure_cache()

    yield

    # Post-test cleanup
    reset_rate_limiter()
    reset_gcra_rate_limiter()
    reset_token_blacklist_cache()
    reset_event_bus()
    clear_jwt_key_cache()
    clear_decode_failure_cache()


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
            # Wait for cancellation to complete, but bound it so a leaked task
            # that swallows CancelledError (or awaits a never-resolving future)
            # can never hang the entire suite. A genuine hang surfaces as a
            # timeout here instead of a silent, indefinite stall.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=10,
                )
            except TimeoutError:
                pass
    except RuntimeError:
        # No event loop in sync tests
        pass

    # Clear any module-level background task references so they don't leak
    # across event loops / tests (e.g. cache stampede-protection tasks).
    try:
        from src.app.core.utils.cache import _background_tasks

        _background_tasks.clear()
    except Exception:
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
def db(setup_db) -> Generator[Session, Any]:
    session = local_session()
    yield session
    session.close()


async def override_get_db() -> AsyncGenerator[AsyncSession]:
    """Override database dependency to use test async session.

    Note: This is a plain async generator, NOT an async context manager.
    FastAPI's Depends() expects an async generator function.
    """
    async with get_testing_session_local()() as session:
        yield session


@pytest.fixture
def client(get_test_app) -> Generator[TestClient, Any]:
    """Sync TestClient fixture with standard mocking for general API tests."""
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1

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

    # Note: We use dependency_overrides for async_get_db because FastAPI
    # needs to properly handle the async generator in dependency injection.
    # Auth flows through install_test_principal only (single seam — overrides
    # get_auth_context/get_optional_auth_context, which get_current_user
    # consumes; no direct get_current_user override).
    app.dependency_overrides[async_get_db] = override_get_db
    from tests.helpers.auth import install_test_principal as _install_principal

    _install_principal(app, fake_superuser, fake_tenant)

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.core.security.request_admission.settings.rate_limit.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA security-budget check (the seam raises; it returns nothing)
        patch(
            "src.app.modules.auth.routers.mfa.check",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        # Mock has_permission_cached so superuser bypass works without a real DB user
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
            yield _client

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(get_test_app) -> Generator[TestClient, Any]:
    """Unauthenticated client fixture for testing auth-required endpoints."""
    app = get_test_app

    # Create fake tenant for tenant middleware
    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1

    from src.app.core.db.database import async_get_db

    # Override DB but NOT authentication - this is an unauthenticated client
    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        patch_setting("RATE_LIMIT_ALGORITHM", "fixed_window"),
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
        patch_setting("BAD_BOT_FILTER_ENABLED", False),
    ):
        with TestClient(app, headers={"User-Agent": "TestClient/1.0"}) as _client:
            yield _client

    # Explicit cleanup
    app.dependency_overrides.clear()


@pytest.fixture
def client_auth(get_test_app) -> Generator[TestClient, Any]:
    """Client fixture for authentication tests - patches tenant and uses SQLite DB."""
    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1

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

    app.dependency_overrides[async_get_db] = override_get_db
    from tests.helpers.auth import install_test_principal as _install_principal_auth

    _install_principal_auth(app, fake_superuser, fake_tenant)

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.core.security.request_admission.settings.rate_limit.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA security-budget check (the seam raises; it returns nothing)
        patch(
            "src.app.modules.auth.routers.mfa.check",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        # Mock has_permission_cached so superuser bypass works without a real DB user
        patch(
            "src.app.core.rbac.dependencies.has_permission_cached",
            new_callable=AsyncMock,
            return_value=True,
        ),
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
        tier_id=1,
    ):
        self.id = id
        self.username = username
        self.email = email
        self.is_superuser = is_superuser
        self.is_active = is_active
        self.tenant_id = tenant_id if tenant_id is not None else self.DEFAULT_TENANT_ID
        self.roles = roles or []
        self.tier_id = tier_id
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
    Auth uses the single test-only principal injector (tests/helpers/auth.py):
    a real AuthContext via dependency_overrides, so `requires_permission`
    exercises the production path.
    """
    from tests.helpers.auth import install_test_principal

    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = MockUser.DEFAULT_TENANT_ID  # Use UUID
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True
    fake_tenant.tier_id = 1

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

    # Note: We use dependency_overrides for async_get_db because FastAPI
    # needs to properly handle the async generator in dependency injection

    app.dependency_overrides[async_get_db] = override_get_db
    install_test_principal(app, fake_superuser, fake_tenant)

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.core.security.request_admission.settings.rate_limit.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA security-budget check (the seam raises; it returns nothing)
        patch(
            "src.app.modules.auth.routers.mfa.check",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
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
    fake_tenant.tier_id = 1

    # Note: We use dependency_overrides for async_get_db instead of patch
    # because override_get_db is an async context manager

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        # Force fixed_window algorithm to use the mocked rate_limiter.check_rate_limit
        patch("src.app.core.security.request_admission.settings.rate_limit.RATE_LIMIT_ALGORITHM", "fixed_window"),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        # Mock MFA security-budget check (the seam raises; it returns nothing)
        patch(
            "src.app.modules.auth.routers.mfa.check",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
    ):
        yield

    # Explicit cleanup - critical for test isolation
    app.dependency_overrides.clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Async database session fixture."""
    async with get_testing_session_local()() as session:
        yield session


@pytest.fixture
async def async_client(get_test_app) -> AsyncGenerator[AsyncClient]:
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
async def unauthenticated_async_client(get_test_app, unauthenticated_mock_context) -> AsyncGenerator[AsyncClient]:
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
    """Headers for superuser requests (principal via test injector)."""
    app = get_test_app
    from tests.helpers.auth import install_test_principal

    # Create superuser mock using MockUser
    fake_superuser = MockUser(
        id=1,
        username="admin",
        email="admin@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=MockUser.DEFAULT_TENANT_ID,
        tier_id=1,
    )

    # Mock has_permission_cached so superuser bypass works without a real DB
    # user row. Without this mock, permission checks hit the DB → 403.
    perm_mock = patch(
        "src.app.core.rbac.dependencies.has_permission_cached",
        new_callable=AsyncMock,
        return_value=True,
    )
    perm_mock.start()

    # Re-install principal (updates existing overrides from full_mock_context)
    install_test_principal(app, fake_superuser, MockUser.DEFAULT_TENANT_ID)
    yield {
        "Authorization": "Bearer superuser",
        "X-Tenant-ID": "test-tenant-integration",
    }
    perm_mock.stop()


@pytest.fixture
async def regular_user_token_headers(get_test_app, full_mock_context):
    """Headers for regular user requests (non-superuser)."""
    app = get_test_app
    from tests.helpers.auth import install_test_principal

    # Create regular user (NOT superuser)
    fake_regular_user = MockUser(
        id=2,
        username="regular_user",
        email="user@example.com",
        is_superuser=False,
        is_active=True,
        tenant_id=MockUser.DEFAULT_TENANT_ID,  # Use UUID
    )

    # Re-install principal (updates existing overrides from full_mock_context)
    install_test_principal(app, fake_regular_user, MockUser.DEFAULT_TENANT_ID)
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

    def exists(self, *keys):
        """Queue an exists command."""
        self._commands.append(("exists", *keys))
        return self

    def incr(self, key):
        """Queue an incr command."""
        self._commands.append(("incr", key))
        return self

    def hset(self, key, mapping=None):
        """Queue a hset command."""
        self._commands.append(("hset", key, mapping))
        return self

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

    def setex(self, key, seconds, value):
        """Queue a setex command (mapped onto the set+ex path)."""
        self._commands.append(("set", key, value, seconds))
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

    def rpush(self, key, *values):
        """Queue a rpush command."""
        for value in values:
            self._commands.append(("rpush", key, value))
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
            if cmd[0] == "exists":
                result = await self.redis.exists(*cmd[1:])
                results.append(result)
            elif cmd[0] == "hset":
                result = await self.redis.hset(cmd[1], mapping=cmd[2])
                results.append(result)
            elif cmd[0] == "zscore":
                result = await self.redis.zscore(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "zadd":
                result = await self.redis.zadd(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "expire":
                result = await self.redis.expire(cmd[1], cmd[2])
                results.append(result)
            elif cmd[0] == "incr":
                result = await self.redis.incr(cmd[1])
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
            elif cmd[0] == "rpush":
                result = await self.redis.rpush(cmd[1], cmd[2])
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

    # ---- set operations (P4: pattern-index invalidation) ----
    def _sets(self):
        if not hasattr(self, "_set_store"):
            self._set_store = {}
        return self._set_store

    async def sadd(self, key, *values):
        s = self._sets().setdefault(key, set())
        s.update(values)
        return len(s)

    async def smembers(self, key):
        return set(self._sets().get(key, set()))

    async def srem(self, key, *values):
        s = self._sets().get(key, set())
        removed = sum(1 for v in values if v in s)
        s.difference_update(values)
        return removed

    async def expire(self, key, seconds):  # sets have no TTL in this fake
        return True

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

    async def hexists(self, key, field):
        return 1

    async def hdel(self, key, *fields):
        return 1

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

    async def rpush(self, key, *values):
        """Push values to the right end of a list (used by audit log buffering)."""
        key = self._get_key(key)
        if key not in self.store:
            self.store[key] = []
        if not isinstance(self.store[key], list):
            self.store[key] = []

        for value in values:
            self.store[key].append(value)
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

    async def evalsha(self, sha, numkeys, *keys_and_args):  # noqa: C901
        """Simulate Lua scripts based on context.

        Handles multiple Redis Lua patterns:
        - GETDEL (matched by registered script SHA from LuaScriptManager)
        - Token blacklist (2 keys, 5+ args): returns 0/1
        - GCRA rate limit (key starts with 'gcra:', 5 args): returns list [allowed, remaining, retry, reset]
        - MFA lockout (key contains 'mfa'/'login_attempts', 3 args): returns count or -1
        - Fixed-window rate limit (default): returns int count
        """
        # --- GETDEL (get_delete script) ---
        # Matched by SHA because LuaScriptManager routes through EVALSHA with
        # only the hex digest — the source-based sniff in eval() can't apply.
        try:
            from src.app.core.utils.lua_manager import get_lua_manager

            if sha == get_lua_manager().get_script("get_delete").sha1:
                key = keys_and_args[0]
                value = self.store.get(key)
                if key in self.store:
                    del self.store[key]
                return value
        except Exception:
            pass

        # --- Merged ban/blacklist+GCRA script (P0-2) ---
        # KEYS[1]=neg_key, KEYS[2]=blacklist_zset, KEYS[3]=authed_gcra,
        # KEYS[4]=ban_key, KEYS[5]=anon_gcra.
        # ARGV = member, now_sec, neg_ttl, now_ms,
        #        a_emit, a_lim, a_burst, a_per, n_emit, n_lim, n_burst, n_per.
        # Returns [is_blacklisted, is_allowed, remaining, retry_after_ms,
        #          is_banned, ban_reason, ban_ttl].
        # Matched by SHA for the same reason as GETDEL above.
        try:
            from src.app.core.utils.lua_manager import get_lua_manager

            if sha == get_lua_manager().get_script("ban_blacklist_gcra_check").sha1:
                neg_key = keys_and_args[0]
                blacklist_key = keys_and_args[1]
                authed_gcra = keys_and_args[2]
                ban_key = keys_and_args[3]
                anon_gcra = keys_and_args[4]
                member = keys_and_args[5]
                now_sec = float(keys_and_args[6]) if len(keys_and_args) > 6 else 0
                now_ms = int(float(keys_and_args[8])) if len(keys_and_args) > 8 else 0

                # Blacklist leg (mirrors token_blacklist_check).
                if neg_key in self.store:
                    is_blacklisted = 0
                else:
                    zset = self.store.get(blacklist_key, {})
                    if not isinstance(zset, dict):
                        zset = {}
                    score = zset.get(member)
                    if score is None:
                        self.store[neg_key] = "1"
                        is_blacklisted = 0
                    elif float(score) < now_sec:
                        if member in zset:
                            del zset[member]
                        self.store[neg_key] = "1"
                        is_blacklisted = 0
                    else:
                        is_blacklisted = 1

                # GCRA leg on the selected candidate (counter emulation,
                # mirroring the standalone GCRA branch below).
                if is_blacklisted == 1:
                    gcra_key = anon_gcra
                    base = 13
                else:
                    gcra_key = authed_gcra
                    base = 9
                limit = int(float(keys_and_args[base + 1]))
                period_ms = int(float(keys_and_args[base + 3]))
                current = self.store.get(gcra_key, 0)
                if isinstance(current, str):
                    current = int(current)
                if current >= limit:
                    is_allowed, remaining = 0, 0
                    retry_after_ms = period_ms
                else:
                    current += 1
                    self.store[gcra_key] = current
                    is_allowed, remaining = 1, limit - current
                    retry_after_ms = 0

                # Ban leg (mirrors ban_and_gcra_check: GET + TTL).
                if ban_key in self.store:
                    is_banned, ban_reason, ban_ttl = 1, str(self.store[ban_key] or ""), 300
                else:
                    is_banned, ban_reason, ban_ttl = 0, "", 0

                return [
                    is_blacklisted,
                    is_allowed,
                    remaining,
                    retry_after_ms,
                    is_banned,
                    ban_reason,
                    ban_ttl,
                ]
        except Exception:
            pass

        # --- Token blacklist script ---
        # KEYS[1]=negative_key, KEYS[2]=blacklist_key, ARGV[1]=token, ARGV[2]=now, ARGV[3]=ttl
        if numkeys == 2 and len(keys_and_args) >= 5:
            negative_key = keys_and_args[0]
            blacklist_key = keys_and_args[1]
            token = keys_and_args[2]

            if negative_key in self.store:
                return 0  # Not blacklisted (cached)

            zset = self.store.get(blacklist_key, {})
            if not isinstance(zset, dict):
                zset = {}
            score = zset.get(token)

            if score is None:
                self.store[negative_key] = "1"
                return 0  # Not blacklisted

            now = float(keys_and_args[3]) if len(keys_and_args) > 3 else 0
            if score < now:
                if token in zset:
                    del zset[token]
                self.store[negative_key] = "1"
                return 0  # Expired - not blacklisted

            return 1  # IS blacklisted

        # Extract primary key for remaining patterns
        primary_key = keys_and_args[0] if keys_and_args else "default_key"

        # --- Auto-ban IP script (P3-1: atomic SETNX + INCR + EXPIRE + DEL) ---
        # KEYS[1]=ban_key (security:ban:{ip}), KEYS[2]=count_key, KEYS[3]=violations_key
        # ARGV[1]=reason, ARGV[2]=ban_duration, ARGV[3]=count_ttl
        # Returns [is_new_ban (0/1), new_ban_count]
        if numkeys == 3 and primary_key.startswith("security:ban:"):
            ban_key = keys_and_args[0]
            count_key = keys_and_args[1]
            violations_key = keys_and_args[2]
            reason = keys_and_args[3] if len(keys_and_args) > 3 else ""
            if ban_key in self.store:
                return [0, 0]  # Already banned (NX would fail)
            self.store[ban_key] = reason
            count = int(self.store.get(count_key, 0) or 0) + 1
            self.store[count_key] = str(count)
            self.store.pop(violations_key, None)
            return [1, count]

        # --- GCRA Rate Limit Script ---
        # Called with 1 key and 4 ARGV: now_ms, emission_interval_ms, limit, period_ms
        if primary_key.startswith("gcra:") and len(keys_and_args) >= 5:
            now_ms = int(float(keys_and_args[1]))
            int(float(keys_and_args[2]))
            limit = int(float(keys_and_args[3]))
            period_ms = int(float(keys_and_args[4]))

            current = self.store.get(primary_key, 0)
            if isinstance(current, str):
                current = int(current)

            if current >= limit:
                return [0, 0, period_ms, now_ms + period_ms]
            else:
                current += 1
                self.store[primary_key] = current
                return [1, limit - current, 0, now_ms + period_ms]

        # --- MFA Lockout Script ---
        # Key contains 'mfa'/'login_attempts', args include max_attempts & lockout_duration
        if len(keys_and_args) >= 3:
            is_mfa_key = "mfa" in primary_key.lower() or "login_attempts" in primary_key.lower()
            if is_mfa_key:
                try:
                    max_attempts = int(keys_and_args[1])
                    current = self.store.get(primary_key)
                    if current and int(current) >= max_attempts:
                        return -1  # Locked out

                    new_count = int(current or 0) + 1
                    self.store[primary_key] = str(new_count)
                    return new_count
                except (ValueError, TypeError):
                    pass

        # --- Fixed-window Rate Limit Script (default) ---
        current = self.store.get(primary_key, 0)
        if isinstance(current, str):
            current = int(current)
        current += 1
        self.store[primary_key] = current
        return current

    async def eval(self, script: str, numkeys: int, *keys_and_args):
        """Execute a Lua script (mock implementation).

        Handles common Redis Lua patterns:
        - GETDEL: Get and delete a key atomically
        - Rate limit scripts: Increment counter
        - Token blacklist scripts: Check blacklist status
        """
        # Detect GETDEL pattern: "local value = redis.call('GET', KEYS[1])"
        if "get" in script.lower() and "del" in script.lower() and numkeys >= 1:
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

    from src.app.core.events import EventBus

    # Create a mock event bus that does nothing
    mock_bus = MagicMock(spec=EventBus)
    mock_bus.publish = AsyncMock()
    mock_bus.publish_sync = MagicMock()
    mock_bus.start = AsyncMock()
    mock_bus.stop = AsyncMock()

    # Patch the event_bus singleton
    monkeypatch.setattr("src.app.core.events.event_bus", mock_bus)

    yield mock_bus


@pytest.fixture(autouse=True)
def mock_redis(request):
    """Mock Redis client with in-memory state.

    Automatically applies to all tests EXCEPT those marked with:
    - @pytest.mark.e2e
    - @pytest.mark.uses_real_redis

    These markers indicate tests that need real Redis connections.
    """
    # Skip mocking for tests that need real Redis
    markers = [m.name for m in request.node.iter_markers()]
    if any(m in markers for m in ("e2e", "uses_real_redis")):
        # Real Redis initialization is handled by init_real_redis_async fixture
        yield None
        return

    fake_redis = FakeRedis()

    # Ensure setup module is imported for patching

    from src.app.core import setup

    # Force attribute to exist for pkgutil.resolve_name used by mock.patch
    if not hasattr(setup, "create_redis_cache_pool"):
        setup.create_redis_cache_pool = setup.create_redis_cache_pool

    from src.app.core.security.session_manager import session_manager
    from src.app.core.security.token_blacklist_cache import _token_cache_key
    from src.app.core.utils.ip_utils import _is_ip_in_trusted_networks, _is_trusted_proxy_cached

    # Clear L1 caches before test to ensure isolation
    from src.app.middleware.tenant import _L1_TENANT_CACHE, _L1_TENANT_PARSED
    from src.app.modules.rbac.service import _L1_RBAC_CACHE

    _L1_TENANT_CACHE.clear()
    _L1_TENANT_PARSED.clear()
    _L1_RBAC_CACHE.clear()
    try:
        from src.app.core.rbac.dependencies import _bitmask_l1

        _bitmask_l1.clear()
    except Exception:
        pass
    try:
        from src.app.modules.users.cache import _auth_info_l1

        _auth_info_l1.clear()
    except Exception:
        pass
    _token_cache_key.cache_clear()
    _is_ip_in_trusted_networks.cache_clear()
    _is_trusted_proxy_cached.cache_clear()

    with (
        # Single seam: every Redis consumer reads cache.client at call time via
        # redis_provider.get_redis(), so this one patch covers all of them.
        patch("src.app.core.utils.cache.client", new=fake_redis),
        # NOT redundant with the line above: RateLimiter.get_client() needs a
        # client with evalsha/pipeline semantics (FakeRedis simulates Lua and
        # returns numeric counts). Test-local doubles of cache.client (bare
        # AsyncMocks, None) would otherwise leak into the fixed-window Lua
        # path, producing AsyncMock counts -> TypeError -> fail-closed 429s
        # (see test_tenant_optimization_e2e). Keep this guard patch.
        patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=fake_redis),
        patch("src.app.core.utils.queue.pool", new=fake_redis),
        patch("src.app.core.redis_provider.get_redis_pool", new=AsyncMock(return_value=fake_redis)),
        patch("src.app.core.redis_provider.close_redis_pool", new=AsyncMock()),
    ):
        # Keep session manager bound to the same Redis backend used by this test
        session_manager.set_redis_client(fake_redis)
        yield fake_redis
        session_manager.set_redis_client(None)
        # Clear L1 caches after test for cleanup
        _L1_TENANT_CACHE.clear()
        _L1_TENANT_PARSED.clear()
        _L1_RBAC_CACHE.clear()
        try:
            from src.app.core.rbac.dependencies import _bitmask_l1

            _bitmask_l1.clear()
        except Exception:
            pass
        try:
            from src.app.modules.users.cache import _auth_info_l1

            _auth_info_l1.clear()
        except Exception:
            pass
        _token_cache_key.cache_clear()
        _is_ip_in_trusted_networks.cache_clear()
        _is_trusted_proxy_cached.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def init_real_redis_async(request):
    """Initialize real Redis client for tests that need it.

    This fixture runs BEFORE tests marked with:
    - @pytest.mark.e2e
    - @pytest.mark.uses_real_redis

    It initializes cache.client with a real Redis connection.
    """
    markers = [m.name for m in request.node.iter_markers()]
    needs_real_redis = any(m in markers for m in ("e2e", "uses_real_redis"))

    if not needs_real_redis:
        yield
        return

    # Initialize real Redis client
    import redis.asyncio as redis

    from src.app.core import redis_provider as redis_factory
    from src.app.core.security.session_manager import session_manager
    from src.app.core.utils import cache
    from src.app.core.utils.gcra_rate_limit import GCRARateLimiter

    # CRITICAL: Reset ALL global state to prevent "Event loop is closed" errors
    # The redis_factory caches the pool globally, and that pool gets bound to
    # a specific event loop. When that loop closes, the pool becomes unusable.
    # We must reset BOTH cache.client/pool AND redis_factory._redis_pool
    # Also reset GCRA rate limiter which caches script SHAs per connection
    cache.client = None
    cache.pool = None
    redis_factory._shared_pool = None  # type: ignore[attr-defined]
    session_manager.set_redis_client(None)

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
        # Bind session manager to current-loop Redis client
        session_manager.set_redis_client(cache.client)

        # Flush before test for isolation
        await cache.client.flushdb()

        # Clear L1 caches (in-memory caches that persist across tests)
        from src.app.middleware.tenant import _L1_TENANT_CACHE, _L1_TENANT_PARSED
        from src.app.modules.rbac.service import _L1_RBAC_CACHE

        _L1_TENANT_CACHE.clear()
        _L1_TENANT_PARSED.clear()
        _L1_RBAC_CACHE.clear()
    except Exception as e:
        # A test marked `uses_real_redis` / `e2e` depends on an EXTERNAL
        # service. When that service is unreachable, `raise` turned the outage
        # into a hard ERROR for every such test — 24 errors in one run when
        # Redis was briefly unavailable, all of them reporting as if the
        # application were broken when nothing under test had run.
        #
        # The correct semantic for an unavailable dependency is to SKIP. Skip
        # only for connectivity failures; anything else (a misconfigured URL, a
        # bad auth, a programming error) still raises, because that is a real
        # defect we must not hide.
        print(f"Warning: Failed to initialize real Redis: {e}")
        # S-08a: the globals below are assigned BEFORE flushdb() runs, so a
        # failure here left cache.client / the GCRA limiter / session_manager's
        # Redis client bound to a DEAD pool. Because cleanup lived after the
        # `yield`, a skip never reached it, and every subsequent test inherited
        # that dead client. Reset them here, on the way out of the failure.
        cache.client = None
        cache.pool = None
        redis_factory._shared_pool = None  # type: ignore[attr-defined]
        gcra_module.gcra_rate_limiter = None
        session_manager.set_redis_client(None)

        if isinstance(
            e,
            ConnectionError | TimeoutError | OSError | redis.ConnectionError | redis.TimeoutError,
        ):
            pytest.skip(f"Redis unavailable, skipping real-Redis test: {e}")
        raise

    try:
        yield
    finally:
        # Cleanup after test - just reset globals, don't try to close
        # (the event loop may be closing and we can't operate on closed loops).
        # `finally`, not a bare tail: teardown must run even if the test body
        # or a later fixture raised.
        cache.client = None
        cache.pool = None
        redis_factory._shared_pool = None  # type: ignore[attr-defined]
        gcra_module.gcra_rate_limiter = None
        session_manager.set_redis_client(None)


@pytest.fixture(autouse=True)
async def cleanup_real_redis(request):
    """Clean up real Redis after integration/e2e tests.

    This fixture runs AFTER tests that use real Redis connections
    (marked with @pytest.mark.e2e or @pytest.mark.uses_real_redis).

    Cleans rate limit keys, auth cache, tenant cache, and token blacklist
    to prevent test isolation issues.
    """
    # Check markers FIRST - skip if not a real Redis test
    markers = [m.name for m in request.node.iter_markers()]
    needs_real_redis = any(m in markers for m in ("e2e", "uses_real_redis"))

    if not needs_real_redis:
        yield
        return

    # SETUP: Clear L1 caches BEFORE test (in-memory caches that persist across tests)
    try:
        from src.app.middleware.tenant import _L1_TENANT_CACHE, _L1_TENANT_PARSED

        _L1_TENANT_CACHE.clear()
        _L1_TENANT_PARSED.clear()
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
    from src.app.modules.users.schemas import UserRead

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
            "src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings",
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
        patcher = patch(
            "src.app.modules.auth.services.sso.SSOService._get_provider_instance",
            return_value=mock_provider,
        )
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

    # Ensure real Postgres test DB has the latest ORM schema.
    # This prevents coverage/e2e failures when migrations were not applied
    # but models (e.g. ai_token_usage relation) are already referenced.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Handle event loop closed during teardown
    try:
        await engine.dispose()
    except RuntimeError:
        # Event loop may be closed during test session teardown
        pass


@pytest_asyncio.fixture
async def real_postgres(real_postgres_engine) -> AsyncGenerator[AsyncSession]:
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
async def real_redis() -> AsyncGenerator[Any]:
    """Real Redis connection for integration/e2e tests.

    Uses function scope (default) to create fresh connections per test,
    preventing 'Future attached to different loop' errors.
    """
    from redis.asyncio import Redis

    # Use TEST_REDIS_PORT env var if set, otherwise use main REDIS_CACHE_PORT
    test_port = int(os.getenv("TEST_REDIS_PORT", str(settings.REDIS_CACHE_PORT)))

    # Handle SecretStr for test environment Redis password
    test_password = os.getenv("TEST_REDIS_PASSWORD")
    if not test_password and settings.REDIS_CACHE_PASSWORD:
        test_password = settings.REDIS_CACHE_PASSWORD.get_secret_value()

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

    Commits the tenant so it is visible to the middleware's separate session.
    """
    from tests.factories import TenantFactory

    tenant = await TenantFactory.create_async(real_postgres)
    await real_postgres.commit()
    return tenant


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
    from src.app.core.security import create_access_token, create_refresh_token
    from tests.factories import UserFactory

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
    from src.app.core.security import create_access_token, create_refresh_token
    from tests.factories import UserFactory

    # Create test user
    user = await UserFactory.create_async(real_postgres, tenant_id=test_tenant.id)

    # Generate tokens for authentication (async functions)
    access_token = await create_access_token(
        data={"sub": user.username, "email": user.email, "tenant_id": str(test_tenant.id)}
    )
    refresh_token = await create_refresh_token(
        data={"sub": user.username, "email": user.email, "tenant_id": str(test_tenant.id)}
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
    from src.app.core.security import create_access_token, create_refresh_token
    from tests.factories import UserFactory

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
        data={"sub": user.username, "email": user.email, "tenant_id": str(test_tenant.id)}
    )
    refresh_token = await create_refresh_token(
        data={"sub": user.username, "email": user.email, "tenant_id": str(test_tenant.id)}
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user,
        "tenant": test_tenant,
    }


@pytest.fixture
async def e2e_client() -> AsyncGenerator[AsyncClient]:
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
    Uses direct attribute save/restore instead of mock.patch to avoid
    Pydantic property deleter issues.
    """
    from contextlib import contextmanager

    @contextmanager
    def _override(**kwargs):
        saved = {}
        for key, value in kwargs.items():
            if hasattr(settings, key):
                saved[key] = getattr(settings, key)
                setattr(settings, key, value)
        try:
            yield
        finally:
            for key, original_value in saved.items():
                setattr(settings, key, original_value)

    return _override


class patch_setting:
    """Context manager to safely patch Settings properties.

    Unlike unittest.mock.patch, this uses setattr/getattr which works
    with Pydantic properties that have setters but no deleters.

    Usage:
        with patch_setting("IP_WHITELIST_ENABLED", True):
            ...
        # Multiple settings:
        with patch_setting("IP_WHITELIST_ENABLED", True, "IP_BLACKLIST_ENABLED", False):
            ...
    """

    def __init__(self, *args):
        """Accept key-value pairs: patch_setting("KEY", value, "KEY2", value2)."""
        if len(args) % 2 != 0:
            raise ValueError("patch_setting requires key-value pairs")
        self.pairs = list(zip(args[::2], args[1::2]))
        self.saved = {}

    def __enter__(self):
        for key, value in self.pairs:
            if hasattr(settings, key):
                self.saved[key] = getattr(settings, key)
                setattr(settings, key, value)
        return self

    def __exit__(self, *args):
        for key, original_value in self.saved.items():
            setattr(settings, key, original_value)
