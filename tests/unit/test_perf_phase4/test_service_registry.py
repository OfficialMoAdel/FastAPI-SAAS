from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import src.app.core.service_registry as service_registry


class MockService:
    def __init__(self, db):
        self.db = db


@pytest.fixture
def mock_db():
    return Mock(spec=AsyncSession)


@pytest.fixture
def clean_registry():
    # Save original state
    original_factories = service_registry._SERVICE_FACTORIES.copy()
    service_registry._SERVICE_FACTORIES.clear()

    # Clear cache if it exists (for when we implement it)
    if hasattr(service_registry, "_service_cache"):
        service_registry._service_cache.clear()

    yield

    # Restore
    service_registry._SERVICE_FACTORIES.clear()
    service_registry._SERVICE_FACTORIES.update(original_factories)


@pytest.mark.asyncio
async def test_service_caching_same_session(mock_db, clean_registry):
    """Test that getting service with same session returns same instance."""
    service_registry.register_service("mock", lambda db: MockService(db))

    s1 = service_registry.get_service("mock", mock_db)
    s2 = service_registry.get_service("mock", mock_db)

    # Should be same instance if cached
    assert s1 is s2
    assert s1.db is mock_db


@pytest.mark.asyncio
async def test_service_caching_different_sessions(clean_registry):
    """Test that different sessions get different instances."""
    db1 = Mock(spec=AsyncSession)
    db2 = Mock(spec=AsyncSession)

    service_registry.register_service("mock", lambda db: MockService(db))

    s1 = service_registry.get_service("mock", db1)
    s2 = service_registry.get_service("mock", db2)

    assert s1 is not s2
    assert s1.db is db1
    assert s2.db is db2
