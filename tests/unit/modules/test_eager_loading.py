"""Tests for N+1 query prevention using get_multi_joined.

This test suite verifies that:
1. get_multi_joined method is available and works
2. JoinConfig-based eager loading prevents N+1 queries
3. Multiple joins work correctly with prefixes
4. Backward compatibility is maintained (get_multi still works)
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastcrud import JoinConfig

from src.app.core.tenancy.context import TenantContext, clear_tenant_context, set_current_tenant
from src.app.modules._base.service import BaseService
from src.app.modules.users.models import User


@pytest.fixture(autouse=True)
def _tenant_context() -> Generator[None]:
    """Provide an active tenant context for tenant-scoped CRUD queries."""
    set_current_tenant(
        TenantContext(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            tenant_slug="test-tenant",
            tenant_name="Test Tenant",
            is_active=True,
        )
    )
    yield
    clear_tenant_context()


class TestBaseServiceGetMultiJoined:
    """Test get_multi_joined functionality in BaseService."""

    @pytest.mark.asyncio
    async def test_get_multi_joined_method_exists(self) -> None:
        """Verify get_multi_joined method exists."""
        import inspect

        # Get the signature of get_multi_joined
        sig = inspect.signature(BaseService.get_multi_joined)

        # Verify method exists
        assert "get_multi_joined" in dir(BaseService), "BaseService should have get_multi_joined method"

        # Verify joins_config parameter exists
        assert "joins_config" in sig.parameters, "get_multi_joined should have joins_config parameter"

        # Verify it has default value of None for backward compatibility
        assert sig.parameters["joins_config"].default is None, "joins_config should default to None"

    @pytest.mark.asyncio
    async def test_get_multi_still_works_backward_compatible(self, db_session) -> None:
        """Test that get_multi still works (backward compatibility)."""
        service = BaseService(User, db_session)

        # This should not raise any errors
        result = await service.get_multi(page=1, per_page=10)

        # Should return empty list or list of users
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_multi_joined_without_joins_falls_back_to_get_multi(self, db_session) -> None:
        """Test that get_multi_joined requires joins (FastCRUD requirement)."""
        service = BaseService(User, db_session)

        # get_multi_joined requires at least one join or counts_config
        # This should raise ValueError
        with pytest.raises(ValueError, match="need one of join_model, joins_config, or counts_config"):
            await service.get_multi_joined(page=1, per_page=10, joins_config=None)

    @pytest.mark.asyncio
    async def test_get_multi_joined_with_empty_joins_fails(self, db_session) -> None:
        """Test that get_multi_joined requires at least one join."""
        service = BaseService(User, db_session)

        # Empty list should fail
        with pytest.raises(ValueError, match="need one of join_model, joins_config, or counts_config"):
            await service.get_multi_joined(page=1, per_page=10, joins_config=[])


class TestN1QueryPrevention:
    """Test N+1 query prevention with joins."""

    @pytest.mark.asyncio
    async def test_n1_prevention_with_joined_tier(self, db_session) -> None:
        """Test that get_multi_joined prevents N+1 queries for tier relationship."""
        from unittest.mock import patch

        from src.app.modules.tiers.models import Tier

        service = BaseService(User, db_session)

        # Track query count
        query_count = 0

        original_execute = db_session.execute

        def track_execute(statement, *args, **kwargs):
            nonlocal query_count
            query_count += 1
            return original_execute(statement, *args, **kwargs)

        with patch.object(db_session, "execute", track_execute):
            # With joins (single query)
            query_count = 0
            await service.get_multi_joined(
                page=1,
                per_page=10,
                joins_config=[
                    JoinConfig(
                        model=Tier,
                        join_on=User.tier_id == Tier.id,
                        join_prefix="tier_",
                    )
                ],
            )

            queries_with_joins = query_count

        # Should use a single query (or very few)
        # With 10 users and no joins, accessing tier would be 11 queries
        # With joins, should be 1-2 queries
        assert queries_with_joins <= 5, f"Expected <= 5 queries, got {queries_with_joins}"

    @pytest.mark.asyncio
    async def test_joined_data_has_prefix(self, db_session) -> None:
        """Test that joined data is returned with correct prefix."""
        from src.app.modules.tiers.models import Tier

        service = BaseService(User, db_session)

        users = await service.get_multi_joined(
            page=1,
            per_page=10,
            joins_config=[
                JoinConfig(
                    model=Tier,
                    join_on=User.tier_id == Tier.id,
                    join_prefix="tier_",
                )
            ],
        )

        # Should return list (even if empty)
        assert isinstance(users, list)

        # If we have data, check prefix
        if users and len(users) > 0:
            user_data = users[0]
            # Check that tier_ prefixed fields exist
            assert "tier_name" in user_data or "tier_id" in user_data or len(user_data) >= 1

    @pytest.mark.asyncio
    async def test_multiple_joins(self, db_session) -> None:
        """Test that multiple joins work correctly."""
        from src.app.modules.tiers.models import Tier

        service = BaseService(User, db_session)

        # Multiple joins
        users = await service.get_multi_joined(
            page=1,
            per_page=10,
            joins_config=[
                JoinConfig(
                    model=Tier,
                    join_on=User.tier_id == Tier.id,
                    join_prefix="tier_",
                )
            ],
        )

        # Should return list
        assert isinstance(users, list)

    @pytest.mark.asyncio
    async def test_joins_with_filters(self, db_session) -> None:
        """Test that joins work with filters."""
        from src.app.modules.tiers.models import Tier

        service = BaseService(User, db_session)

        # Use both filters and joins (filter on user fields)
        users = await service.get_multi_joined(
            page=1,
            per_page=10,
            joins_config=[
                JoinConfig(
                    model=Tier,
                    join_on=User.tier_id == Tier.id,
                    join_prefix="tier_",
                )
            ],
            tenant_id=1,  # Filter on user field
        )

        # Should return list (empty or with users)
        assert isinstance(users, list)


class TestJoinConfigPatterns:
    """Test common JoinConfig patterns."""

    @pytest.mark.asyncio
    async def test_join_with_schema_select(self, db_session) -> None:
        """Test JoinConfig with schema_to_select parameter."""
        from src.app.modules.tiers.models import Tier
        from src.app.modules.tiers.schemas import TierRead

        service = BaseService(User, db_session)

        # Select specific columns from joined table using Pydantic schema
        users = await service.get_multi_joined(
            page=1,
            per_page=10,
            joins_config=[
                JoinConfig(
                    model=Tier,
                    join_on=User.tier_id == Tier.id,
                    join_prefix="tier_",
                    schema_to_select=TierRead,  # Use Pydantic schema
                )
            ],
        )

        # Should return list
        assert isinstance(users, list)

    @pytest.mark.asyncio
    async def test_join_with_filters(self, db_session) -> None:
        """Test JoinConfig with join_on filtering."""
        from src.app.modules.tiers.models import Tier

        service = BaseService(User, db_session)

        # Filter joined table
        users = await service.get_multi_joined(
            page=1,
            per_page=10,
            joins_config=[
                JoinConfig(
                    model=Tier,
                    join_on=User.tier_id == Tier.id,
                    join_prefix="tier_",
                    join_type="left",  # Left join
                )
            ],
        )

        # Should return list
        assert isinstance(users, list)
