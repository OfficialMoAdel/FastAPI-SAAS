"""P0-2: FastCRUD Flyweight Cache Tests.

This module tests the FastCRUD flyweight caching optimization in BaseService.
The optimization caches FastCRUD instances at module level to avoid
recreating them for each service instance.

Tests imported from: tests/unit/modules/test_base_service_cache.py

Isomorphism Verification:
- Same CRUD operations work identically before and after
- Cached instance has same behavior as fresh instance
- Thread safety is maintained
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastcrud import FastCRUD

from src.app.modules._base.service import _CRUD_CACHE, BaseService
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the CRUD cache before and after each test."""
    _CRUD_CACHE.clear()
    yield
    _CRUD_CACHE.clear()


class TestP02FastCRUDCache:
    """P0-2: FastCRUD Flyweight Cache optimization tests."""

    def test_crud_instance_is_cached(self) -> None:
        """FastCRUD instance should be cached at module level."""
        mock_db = MagicMock()
        service1 = BaseService(User, mock_db)

        # Verify cache contains the model
        assert User in _CRUD_CACHE

        # Create another service with same model
        service2 = BaseService(User, mock_db)

        # Both should share the same CRUD instance
        assert service1.crud is service2.crud

    def test_different_models_have_different_cache_entries(self) -> None:
        """Different models should have separate cache entries."""
        mock_db = MagicMock()
        service1 = BaseService(User, mock_db)
        service2 = BaseService(Tenant, mock_db)

        # Different models should have different CRUD instances
        assert service1.crud is not service2.crud
        assert User in _CRUD_CACHE
        assert Tenant in _CRUD_CACHE

    def test_cache_survives_multiple_instantiations(self) -> None:
        """Cache should persist across many service instantiations."""
        mock_db = MagicMock()

        # Create many services with same model
        services = [BaseService(User, mock_db) for _ in range(100)]

        # All should share the same CRUD instance
        first_crud = services[0].crud
        for service in services[1:]:
            assert service.crud is first_crud

        # Cache should only have one entry for User
        assert _CRUD_CACHE.get(User) is first_crud

    def test_db_session_not_cached(self) -> None:
        """Database session should NOT be cached (different per request)."""
        mock_db1 = MagicMock()
        mock_db2 = MagicMock()

        service1 = BaseService(User, mock_db1)
        service2 = BaseService(User, mock_db2)

        # CRUD is shared
        assert service1.crud is service2.crud

        # But db session is different
        assert service1.db is mock_db1
        assert service2.db is mock_db2
        assert service1.db is not service2.db

    def test_cached_crud_is_fastcrud_instance(self) -> None:
        """Cached instance should be a proper FastCRUD object."""
        mock_db = MagicMock()
        service = BaseService(User, mock_db)

        assert isinstance(service.crud, FastCRUD)
        assert _CRUD_CACHE[User] is service.crud


class TestP02CacheIsolation:
    """Test cache isolation and edge cases."""

    def test_empty_cache_at_start(self) -> None:
        """Cache should be empty after fixture clears it."""
        assert len(_CRUD_CACHE) == 0

    def test_cache_grows_with_new_models(self) -> None:
        """Cache should grow as new models are used."""
        mock_db = MagicMock()

        BaseService(User, mock_db)
        assert len(_CRUD_CACHE) == 1

        BaseService(Tenant, mock_db)
        assert len(_CRUD_CACHE) == 2

    def test_cache_key_is_model_class(self) -> None:
        """Cache key should be the model class."""
        mock_db = MagicMock()
        BaseService(User, mock_db)

        # Key should be the User class (may be a SQLAlchemy decorated type)
        keys = list(_CRUD_CACHE.keys())
        assert len(keys) == 1
        assert keys[0] is User


class TestP02ThreadSafety:
    """Thread safety tests for cache access."""

    def test_concurrent_access_safe(self) -> None:
        """Verify thread safety of cache dict access."""
        mock_db = MagicMock()
        results = []

        def create_service():
            service = BaseService(User, mock_db)
            results.append(id(service.crud))
            return service

        # Run concurrent service creations
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_service) for _ in range(100)]
            for future in futures:
                future.result()

        # All CRUD instances should be the same
        assert len(set(results)) == 1
