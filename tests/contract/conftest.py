"""Contract test fixtures.

Provides fixtures for API contract testing including authenticated
and unauthenticated clients for testing auth requirements.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Re-export fixtures from root conftest
from tests.conftest import (
    MockUser,
    get_testing_session_local,
    override_get_db,
)


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
def get_contract_test_app():
    """Get a fresh app instance for contract tests."""
    from src.app.main import app

    app.dependency_overrides.clear()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def contract_mock_context(get_contract_test_app):
    """Mock context for contract tests with authentication."""
    app = get_contract_test_app

    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-contract"
    fake_tenant.is_active = True

    from src.app.api.deps import get_current_user
    from src.app.core.db.database import async_get_db

    fake_superuser = MockUser(
        id=1,
        username="contract_admin",
        email="contract_admin@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
    )

    async def mock_get_current_user():
        return fake_superuser

    app.dependency_overrides[async_get_db] = override_get_db
    app.dependency_overrides[get_current_user] = mock_get_current_user

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch("src.app.core.db.database.async_get_db", side_effect=override_get_db),
    ):
        yield

    app.dependency_overrides.clear()


@pytest.fixture
async def unauthenticated_contract_context(get_contract_test_app):
    """Mock context WITHOUT authentication for testing auth-required endpoints."""
    app = get_contract_test_app

    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-contract"
    fake_tenant.is_active = True

    from src.app.core.db.database import async_get_db

    app.dependency_overrides[async_get_db] = override_get_db

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch("src.app.core.db.database.async_get_db", side_effect=override_get_db),
    ):
        yield

    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(get_contract_test_app, contract_mock_context) -> AsyncGenerator[AsyncClient, None]:
    """Async client fixture for contract tests."""
    app = get_contract_test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-contract"},
    ) as ac:
        yield ac


@pytest.fixture
async def unauthenticated_async_client(
    get_contract_test_app, unauthenticated_contract_context
) -> AsyncGenerator[AsyncClient, None]:
    """Async client WITHOUT authentication for testing auth-required endpoints."""
    app = get_contract_test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-contract"},
    ) as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async database session fixture for contract tests."""
    from sqlalchemy import text

    async with get_testing_session_local()() as session:
        # Set SQLite busy timeout to handle concurrent access
        await session.execute(text("PRAGMA busy_timeout = 30000"))
        yield session


@pytest.fixture
async def superuser_token_headers(get_contract_test_app, contract_mock_context):
    """Headers for superuser requests."""
    app = get_contract_test_app
    from src.app.api.deps import get_current_user

    fake_superuser = MockUser(
        id=1,
        username="contract_superuser",
        email="superuser@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
    )

    app.dependency_overrides[get_current_user] = lambda: fake_superuser
    return {
        "Authorization": "Bearer superuser",
        "X-Tenant-ID": "test-tenant-contract",
    }


@pytest.fixture
async def regular_user_token_headers(get_contract_test_app, contract_mock_context):
    """Headers for regular user requests (non-superuser)."""
    app = get_contract_test_app
    from src.app.api.deps import get_current_user

    fake_regular_user = MockUser(
        id=2,
        username="regular_user",
        email="user@example.com",
        is_superuser=False,
        is_active=True,
        tenant_id=1,
    )

    app.dependency_overrides[get_current_user] = lambda: fake_regular_user
    return {
        "Authorization": "Bearer regular",
        "X-Tenant-ID": "test-tenant-contract",
    }
