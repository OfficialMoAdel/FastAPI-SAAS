"""Tests for FastCRUD flyweight caching in BaseService (OPT P0-2)."""

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
    """Clear the CRUD cache before each test."""
    _CRUD_CACHE.clear()
    yield
    _CRUD_CACHE.clear()


class TestFastCRUDCache:
    """Tests for FastCRUD flyweight caching."""

    def test_crud_cache_reuses_instance(self) -> None:
        """Verify same FastCRUD instance is returned for same model."""
        mock_db = MagicMock()

        service1 = BaseService(User, mock_db)
        service2 = BaseService(User, mock_db)

        # Should be the exact same FastCRUD instance
        assert service1.crud is service2.crud
        assert id(service1.crud) == id(service2.crud)

        # Cache should have only one entry for User
        assert User in _CRUD_CACHE
        assert len([k for k in _CRUD_CACHE if k is User]) == 1

    def test_crud_cache_different_models(self) -> None:
        """Verify different FastCRUD instances for different models."""
        mock_db = MagicMock()

        user_service = BaseService(User, mock_db)
        tenant_service = BaseService(Tenant, mock_db)

        # Should be different FastCRUD instances
        assert user_service.crud is not tenant_service.crud
        assert id(user_service.crud) != id(tenant_service.crud)

        # Cache should have entries for both models
        assert User in _CRUD_CACHE
        assert Tenant in _CRUD_CACHE
        assert len(_CRUD_CACHE) >= 2

    def test_crud_instance_is_fastcrud(self) -> None:
        """Verify cached instance is a proper FastCRUD object."""
        mock_db = MagicMock()

        service = BaseService(User, mock_db)

        assert isinstance(service.crud, FastCRUD)
        assert _CRUD_CACHE[User] is service.crud

    def test_db_session_not_cached(self) -> None:
        """Verify db session remains per-instance (not shared)."""
        mock_db1 = MagicMock()
        mock_db2 = MagicMock()

        service1 = BaseService(User, mock_db1)
        service2 = BaseService(User, mock_db2)

        # CRUD is shared
        assert service1.crud is service2.crud

        # But db sessions are different
        assert service1.db is not service2.db
        assert service1.db is mock_db1
        assert service2.db is mock_db2

    def test_cache_persistence_across_instantiations(self) -> None:
        """Verify cache persists across many service instantiations."""
        mock_db = MagicMock()

        # Create 100 service instances
        services = [BaseService(User, mock_db) for _ in range(100)]

        # All should share the same CRUD instance
        first_crud = services[0].crud
        for service in services[1:]:
            assert service.crud is first_crud

        # Cache should have exactly one User entry
        assert _CRUD_CACHE[User] is first_crud

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
        assert len(set(results)) == 1, "All concurrent accesses should return same CRUD instance"

    def test_model_attribute_preserved(self) -> None:
        """Verify model attribute is correctly set per service."""
        mock_db = MagicMock()

        user_service = BaseService(User, mock_db)
        tenant_service = BaseService(Tenant, mock_db)

        assert user_service.model is User
        assert tenant_service.model is Tenant

    def test_cache_hit_avoids_instantiation(self) -> None:
        """Verify cache hit doesn't create new FastCRUD."""
        mock_db = MagicMock()

        # First instantiation - cache miss
        service1 = BaseService(User, mock_db)
        original_crud = service1.crud

        # Get the current cache state
        cached_crud = _CRUD_CACHE[User]
        assert cached_crud is original_crud

        # Second instantiation - should be cache hit
        service2 = BaseService(User, mock_db)

        # Verify same object (not a new instantiation)
        assert service2.crud is original_crud
        assert _CRUD_CACHE[User] is original_crud


class TestCachePerformance:
    """Performance-related tests for cache behavior."""

    def test_many_models_cached_independently(self) -> None:
        """Verify multiple models are cached independently."""
        mock_db = MagicMock()

        # Import additional models for testing
        from src.app.modules.posts.models import Post

        user_service = BaseService(User, mock_db)
        tenant_service = BaseService(Tenant, mock_db)
        post_service = BaseService(Post, mock_db)

        # All should be cached
        assert User in _CRUD_CACHE
        assert Tenant in _CRUD_CACHE
        assert Post in _CRUD_CACHE

        # All should be different instances
        assert user_service.crud is not tenant_service.crud
        assert tenant_service.crud is not post_service.crud
        assert user_service.crud is not post_service.crud

    def test_cache_reduces_object_creation(self) -> None:
        """Verify cache reduces number of FastCRUD instantiations."""
        mock_db = MagicMock()

        # Clear and count initial state
        initial_cache_size = len(_CRUD_CACHE)

        # Create 1000 services for the same model
        for _ in range(1000):
            BaseService(User, mock_db)

        # Should have only added 1 entry to cache (for User)
        assert len(_CRUD_CACHE) == initial_cache_size + 1
        assert User in _CRUD_CACHE
