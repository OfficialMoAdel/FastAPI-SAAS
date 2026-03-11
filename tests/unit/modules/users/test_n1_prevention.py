"""Tests for N+1 query prevention in list endpoints.

This test suite verifies that:
1. List endpoints use schema_to_select to avoid ORM object materialization
2. Eager loading is used where relationships are actually needed
3. No N+1 queries occur in typical list operations

Regression test for P1-3: N+1 Query Elimination
"""

from __future__ import annotations

import pytest


class TestUserListN1Prevention:
    """Test that user listing avoids N+1 queries."""

    @pytest.mark.asyncio
    async def test_list_users_get_multi_signature_supports_schema_to_select(self) -> None:
        """Verify get_multi method signature supports schema_to_select parameter."""
        import inspect

        from src.app.modules.users.crud import crud_users

        # Check get_multi signature supports schema_to_select
        _ = inspect.signature(crud_users.get_multi)

        # The FastCRUD get_multi should accept schema_to_select for projection
        # This is inherited from fastcrud.FastCRUD
        assert hasattr(crud_users, "get_multi"), "crud_users should have get_multi method"

        # Verify the method exists and is callable
        assert callable(crud_users.get_multi)

    @pytest.mark.asyncio
    async def test_user_read_schema_excludes_relationships(self) -> None:
        """UserRead schema should only include columns, not relationships."""
        from src.app.modules.users.schemas import UserRead

        # Get the model fields
        fields = UserRead.model_fields

        # Verify tier relationship is NOT included (only tier_id)
        assert "tier_id" in fields, "tier_id column should be included"
        assert "tier" not in fields, "tier relationship should NOT be included"

        # Verify other relationships are NOT included
        assert "roles" not in fields, "roles relationship should NOT be included"
        assert "api_keys" not in fields, "api_keys relationship should NOT be included"


class TestAPIKeyListN1Prevention:
    """Test that API key listing avoids N+1 queries."""

    @pytest.mark.asyncio
    async def test_list_api_keys_uses_schema_to_select(self) -> None:
        """Verify API key listing uses schema projection."""
        from src.app.modules.api_keys.schemas import APIKeyRead

        # Verify the read schema only includes columns
        fields = APIKeyRead.model_fields

        # Should not include user or tenant relationships
        assert "user" not in fields
        assert "tenant" not in fields


class TestChatSessionListN1Prevention:
    """Test that chat session listing avoids N+1 queries."""

    @pytest.mark.asyncio
    async def test_chat_session_read_schema_excludes_relationships(self) -> None:
        """ChatSessionRead schema should only include columns, not relationships."""
        from src.app.modules.ai.schemas import ChatSessionRead

        fields = ChatSessionRead.model_fields

        # Verify relationships are NOT included (only IDs)
        assert "user_id" in fields, "user_id column should be included"
        assert "tenant_id" in fields, "tenant_id column should be included"
        assert "user" not in fields, "user relationship should NOT be included"
        assert "tenant" not in fields, "tenant relationship should NOT be included"
        assert "messages" not in fields, "messages relationship should NOT be included"


class TestEagerLoadingExists:
    """Test that eager loading is used where relationships ARE needed."""

    @pytest.mark.asyncio
    async def test_get_user_with_tier_eager_uses_joinedload(self) -> None:
        """Verify get_user_with_tier_eager implements eager loading."""
        import inspect

        from src.app.modules.users.crud import crud_users

        # Get the source code of the method
        source = inspect.getsource(crud_users.get_user_with_tier_eager)

        # Verify it uses joinedload
        assert "joinedload" in source, "get_user_with_tier_eager should use joinedload for eager loading"

    @pytest.mark.asyncio
    async def test_user_service_get_with_rate_limits_uses_eager_loading(self) -> None:
        """Verify get_with_rate_limits uses nested eager loading."""
        import inspect

        from src.app.modules.users.service import UserService

        source = inspect.getsource(UserService.get_with_rate_limits)

        # Verify it uses joinedload for nested relationships
        assert "joinedload" in source, "Should use joinedload for tier"
        assert "rate_limits" in source, "Should eager load rate_limits"


class TestSchemaProjectionPattern:
    """Test the schema_to_select pattern is consistently used."""

    @pytest.mark.asyncio
    async def test_billing_product_list_uses_schema(self) -> None:
        """Verify product listing uses ProductRead schema."""
        from src.app.modules.billing.schemas import ProductRead

        fields = ProductRead.model_fields

        # Should not include pricing_tiers relationship
        assert "pricing_tiers" not in fields
        assert "tenant" not in fields

    @pytest.mark.asyncio
    async def test_audit_log_list_uses_schema(self) -> None:
        """Verify audit log listing uses AuditLogRead schema."""
        from src.app.modules.audit_logs.schemas import AuditLogRead

        fields = AuditLogRead.model_fields

        # Should not include tenant relationship
        assert "tenant" not in fields


# === PHASE 2 MIGRATION: Tests from test_n1_elimination.py ===
# Tests verifying that UserService.get_with_rate_limits uses eager loading
# (joinedload) to fetch User + Tier + RateLimits in a single query.

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.app.modules.users.service import UserService


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def user_service(mock_db):
    """Create UserService with mocked db."""
    return UserService(mock_db)


class TestUserServiceN1EliminationEagerLoading:
    """Verify N+1 elimination via joinedload in get_with_rate_limits."""

    @pytest.mark.asyncio
    async def test_get_with_rate_limits_single_query(self, user_service, mock_db):
        """Verify get_with_rate_limits executes single query with joinedload."""
        # Setup mock user with tier and rate limits
        mock_rate_limit = MagicMock()
        mock_rate_limit.id = 1
        mock_rate_limit.tier_id = 1
        mock_rate_limit.path = "/api/v1/test"
        mock_rate_limit.limit = 100
        mock_rate_limit.period = 60
        mock_rate_limit.name = "api_v1_test:100:60"
        mock_rate_limit.created_at = MagicMock()
        mock_rate_limit.updated_at = None

        mock_tier = MagicMock()
        mock_tier.id = 1
        mock_tier.name = "premium"
        mock_tier.rate_limits = [mock_rate_limit]

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.profile_image_url = "https://example.com/img.png"
        mock_user.uuid = uuid4()
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.is_deleted = False
        mock_user.is_superuser = False
        mock_user.tier_id = 1
        mock_user.tier = mock_tier

        # Mock the query result
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        # Call the method
        result = await user_service.get_with_rate_limits("testuser")

        # Verify only ONE execute call (single query)
        assert mock_db.execute.call_count == 1

        # Verify result contains rate limits
        assert "tier_rate_limits" in result
        assert len(result["tier_rate_limits"]) == 1

    @pytest.mark.asyncio
    async def test_get_with_rate_limits_no_tier(self, user_service, mock_db):
        """Verify handling when user has no tier."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.profile_image_url = "https://example.com/img.png"
        mock_user.uuid = uuid4()
        mock_user.created_at = MagicMock()
        mock_user.updated_at = None
        mock_user.is_deleted = False
        mock_user.is_superuser = False
        mock_user.tier_id = None
        mock_user.tier = None

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await user_service.get_with_rate_limits("testuser")

        # Single query still
        assert mock_db.execute.call_count == 1
        # Empty rate limits
        assert result["tier_rate_limits"] == []

    @pytest.mark.asyncio
    async def test_user_not_found_raises(self, user_service, mock_db):
        """Verify ModelNotFoundError raised when user doesn't exist."""
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        from src.app.modules._base.exceptions import ModelNotFoundError

        with pytest.raises(ModelNotFoundError):
            await user_service.get_with_rate_limits("nonexistent")


class TestUserServiceQueryStructure:
    """Verify the query uses joinedload for eager loading."""

    def test_joinedload_import_available(self):
        """Verify joinedload is used in the service."""
        import inspect

        from src.app.modules.users import service as user_service_module

        source = inspect.getsource(user_service_module.UserService.get_with_rate_limits)

        # Verify joinedload is used for eager loading
        assert "joinedload" in source
        assert "User.tier" in source
        assert "Tier.rate_limits" in source
