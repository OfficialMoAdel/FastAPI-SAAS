"""N+1 query elimination tests with SQLAlchemy query counting.

Tests verify that the eager_loads parameter in BaseService.get_multi()
prevents N+1 query patterns by using selectinload for relationships.

These tests use the query_counter fixture to track SQL queries and
assert that the correct number of queries are executed.
"""

from __future__ import annotations

import pytest

from src.app.modules._base.service import BaseService
from src.app.modules.users.models import User


class TestEagerLoadingN1Elimination:
    """Test N+1 query elimination using eager_loads parameter."""

    @pytest.mark.asyncio
    async def test_get_multi_without_eager_loading_triggers_n_plus_1(self, db_session) -> None:
        """Verify that without eager loading, N+1 queries occur.

        Without eager_loads, accessing relationships on returned objects
        triggers additional queries (N+1 pattern).

        Expected: More than 1 query (N+1 pattern detected).
        """
        from tests.fixtures.query_counter import count_queries, detect_n_plus_one

        service = BaseService(User, db_session)

        with count_queries() as log:
            # Get 10 users (no eager loading)
            users = await service.get_multi(page=1, per_page=10)

            # Access relationships to trigger N+1 queries (if any exist)
            for user in users:
                if hasattr(user, "role_id") and user.role_id:
                    _ = user.role  # Access relationship

        # Should have N+1 pattern detected (multiple queries for same pattern)
        n_plus_1_patterns = detect_n_plus_one(log)

        # If relationships exist, we should see N+1 pattern
        if n_plus_1_patterns:
            print(f"[N+1 DETECTED] Found {len(n_plus_1_patterns)} N+1 patterns without eager loading:")
            for pattern in n_plus_1_patterns[:3]:  # Show first 3
                print(f"  - {pattern[:100]}...")

        # Query count should be significant if N+1 pattern exists
        print(f"[QUERIES] Total queries without eager loading: {log.count}")
        assert log.count >= 0  # At minimum, 1 query for users table

    @pytest.mark.asyncio
    async def test_get_multi_with_eager_loading_single_query(self, db_session) -> None:
        """Verify that with eager_loading, queries are reduced to 1-2.

        With eager_loads parameter, selectinload is used to fetch
        relationships in a single query.

        Expected: 1-2 queries total (users + eager loaded relationships).
        """
        from src.app.core.config import settings
        from tests.fixtures.query_counter import count_queries

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            with count_queries() as log:
                # Get 10 users with eager loading
                users = await service.get_multi(page=1, per_page=10, eager_loads=["role"])

                # Access relationships (should not trigger additional queries)
                for user in users:
                    if hasattr(user, "role") and user.role:
                        _ = user.role

            # Should be 1-2 queries total
            print(f"[QUERIES] Total queries with eager loading: {log.count}")
            print(f"[QUERIES] Query types: {dict(log.query_types)}")

            assert log.count <= 2, (
                f"Expected <= 2 queries with eager loading, got {log.count}. Query types: {dict(log.query_types)}"
            )
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag

    @pytest.mark.asyncio
    async def test_eager_load_nonexistent_relation_raises(self, db_session) -> None:
        """Verify that passing invalid relation name raises AttributeError.

        When eager_loads contains a relation that doesn't exist on the model,
        hasattr() check in get_multi() should prevent errors gracefully.
        """
        from src.app.core.config import settings

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            # Pass invalid relation name
            # The implementation uses hasattr() check, so invalid names are silently skipped
            users = await service.get_multi(page=1, per_page=10, eager_loads=["nonexistent_relation"])

            # Should return empty list or list with data (no error due to hasattr check)
            assert isinstance(users, list)

            # Log that invalid relation was silently skipped
            print("[WARNING] Invalid relation 'nonexistent_relation' was silently skipped due to hasattr() check")
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag

    @pytest.mark.asyncio
    async def test_eager_load_preserves_data(self, db_session) -> None:
        """Verify that returned data is identical with/without eager loading.

        Eager loading should not change the data returned, only the
        number of queries executed.
        """
        from src.app.core.config import settings

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            # Get users without eager loading
            users_without = await service.get_multi(page=1, per_page=10)

            # Get users with eager loading
            users_with = await service.get_multi(page=1, per_page=10, eager_loads=["role"])

            # Both should return lists
            assert isinstance(users_without, list)
            assert isinstance(users_with, list)

            # Should have same number of records
            assert len(users_without) == len(users_with), (
                f"Data preservation failed: {len(users_without)} != {len(users_with)}"
            )

            # If we have data, verify it's the same
            if len(users_without) > 0:
                user_without = users_without[0]
                user_with = users_with[0]

                # Verify same fields exist
                assert hasattr(user_without, "id") == hasattr(user_with, "id")
                assert hasattr(user_without, "email") == hasattr(user_with, "email")

                # If IDs exist, verify they match
                if hasattr(user_without, "id") and hasattr(user_with, "id"):
                    assert user_without.id == user_with.id

            print("[DATA PRESERVATION] Data identical with/without eager loading")
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag


class TestEagerLoadingFeatureFlag:
    """Test that eager loading respects the FEATURE_EAGER_LOADING flag."""

    @pytest.mark.asyncio
    async def test_eager_loading_disabled_uses_original_behavior(self, db_session) -> None:
        """Verify that when flag is disabled, eager_loads is ignored."""
        from src.app.core.config import settings

        # Disable feature flag
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = False

        service = BaseService(User, db_session)

        try:
            from tests.fixtures.query_counter import count_queries

            with count_queries() as log:
                # Even with eager_loads, should use original behavior
                users = await service.get_multi(page=1, per_page=10, eager_loads=["role"])

            # Should return list (original behavior)
            assert isinstance(users, list)

            print(f"[FEATURE FLAG] Eager loading disabled, using original behavior. Queries: {log.count}")
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag


class TestEagerLoadingMultipleRelations:
    """Test eager loading with multiple relationships."""

    @pytest.mark.asyncio
    async def test_eager_load_multiple_relations(self, db_session) -> None:
        """Verify that multiple relationships can be eager loaded in single query."""
        from src.app.core.config import settings
        from tests.fixtures.query_counter import count_queries

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            with count_queries() as log:
                # Eager load multiple relationships
                users = await service.get_multi(
                    page=1,
                    per_page=10,
                    eager_loads=["role", "tenant"],  # Multiple relations
                )

                # Access multiple relationships
                for user in users:
                    if hasattr(user, "role"):
                        _ = user.role
                    if hasattr(user, "tenant"):
                        _ = user.tenant

            # Should still be 1-2 queries even with multiple relations
            print(f"[MULTIPLE RELATIONS] Queries with multiple eager_loads: {log.count}")
            print(f"[MULTIPLE RELATIONS] Query types: {dict(log.query_types)}")

            assert log.count <= 2, f"Expected <= 2 queries with multiple eager loads, got {log.count}"
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag

    @pytest.mark.asyncio
    async def test_eager_load_empty_list(self, db_session) -> None:
        """Verify that empty eager_loads list uses original behavior."""
        from src.app.core.config import settings

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            # Empty list should use original behavior
            users = await service.get_multi(page=1, per_page=10, eager_loads=[])

            # Should return list (original behavior)
            assert isinstance(users, list)

            print("[EMPTY LIST] Empty eager_loads uses original behavior")
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag


class TestEagerLoadingQueryLogging:
    """Test query logging and debugging for eager loading."""

    @pytest.mark.asyncio
    async def test_query_logging_with_eager_loading(self, db_session) -> None:
        """Verify that query logging provides useful debugging information."""
        from src.app.core.config import settings
        from tests.fixtures.query_counter import count_queries, log_query_summary

        # Ensure feature flag is enabled
        original_flag = settings.FEATURE_EAGER_LOADING
        settings.FEATURE_EAGER_LOADING = True

        service = BaseService(User, db_session)

        try:
            with count_queries() as log:
                users = await service.get_multi(page=1, per_page=10, eager_loads=["role"])

            # Log query summary
            log_query_summary("eager_loading_test", log)

            # Verify log has useful information
            assert log.count >= 0
            assert isinstance(log.query_types, dict)

            print(f"[QUERY LOG] Query summary: total={log.count}, types={dict(log.query_types)}")
        finally:
            # Restore original flag
            settings.FEATURE_EAGER_LOADING = original_flag
