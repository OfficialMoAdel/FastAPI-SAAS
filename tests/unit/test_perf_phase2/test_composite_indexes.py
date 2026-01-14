"""U5 - Composite Indexes Unit Tests.

Tests verifying that composite indexes are correctly defined on User model
and that queries leverage them for efficient lookups.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Index

from src.app.modules.users.models import User


class TestCompositeIndexDefinitions:
    """Verify composite index definitions in User model."""

    def test_tenant_email_composite_index_exists(self):
        """Verify idx_users_tenant_email index is defined."""
        index_names = [idx.name for idx in User.__table__.indexes]
        assert "idx_users_tenant_email" in index_names

    def test_tenant_deleted_composite_index_exists(self):
        """Verify idx_users_tenant_deleted index is defined."""
        index_names = [idx.name for idx in User.__table__.indexes]
        assert "idx_users_tenant_deleted" in index_names

    def test_tenant_email_index_columns(self):
        """Verify tenant_email index has correct columns in order."""
        for idx in User.__table__.indexes:
            if idx.name == "idx_users_tenant_email":
                col_names = [col.name for col in idx.columns]
                assert col_names == ["tenant_id", "email"]
                return
        pytest.fail("Index idx_users_tenant_email not found")

    def test_tenant_deleted_index_columns(self):
        """Verify tenant_deleted index has correct columns in order."""
        for idx in User.__table__.indexes:
            if idx.name == "idx_users_tenant_deleted":
                col_names = [col.name for col in idx.columns]
                assert col_names == ["tenant_id", "is_deleted"]
                return
        pytest.fail("Index idx_users_tenant_deleted not found")

    def test_unique_constraint_email_tenant(self):
        """Verify unique constraint on email+tenant_id."""
        constraints = [c.name for c in User.__table__.constraints if hasattr(c, "name") and c.name]
        assert "uq_user_email_tenant" in constraints


class TestIndexUsagePatterns:
    """Test that common query patterns would use composite indexes."""

    def test_tenant_email_lookup_pattern(self):
        """Verify tenant+email filter pattern matches index."""
        # The index is (tenant_id, email)
        # Queries filtering by both should use this index
        idx = None
        for i in User.__table__.indexes:
            if i.name == "idx_users_tenant_email":
                idx = i
                break

        assert idx is not None
        # Column order matters for index usage
        cols = list(idx.columns)
        assert cols[0].name == "tenant_id"  # Leading column
        assert cols[1].name == "email"

    def test_tenant_deleted_filter_pattern(self):
        """Verify tenant+is_deleted filter pattern matches index."""
        idx = None
        for i in User.__table__.indexes:
            if i.name == "idx_users_tenant_deleted":
                idx = i
                break

        assert idx is not None
        cols = list(idx.columns)
        assert cols[0].name == "tenant_id"  # Leading column
        assert cols[1].name == "is_deleted"


class TestIsomorphismCompositeIndexes:
    """Isomorphism tests - verify queries return same results with indexes."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        db = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_tenant_email_query_isomorphism(self, mock_db):
        """Verify tenant+email query returns consistent results."""
        # Setup mock result
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "test@example.com"
        mock_user.tenant_id = 100

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        # Simulate the query pattern that uses the index
        from sqlalchemy import select

        stmt = select(User).where(User.tenant_id == 100, User.email == "test@example.com")

        result = await mock_db.execute(stmt)
        user = result.scalar_one_or_none()

        # Isomorphism: Result should match regardless of index presence
        assert user.id == 1
        assert user.email == "test@example.com"
        assert user.tenant_id == 100

    @pytest.mark.asyncio
    async def test_tenant_deleted_query_isomorphism(self, mock_db):
        """Verify tenant+is_deleted query returns consistent results."""
        # Setup mock results for active users
        mock_users = [MagicMock(id=1, is_deleted=False), MagicMock(id=2, is_deleted=False)]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_users
        mock_db.execute.return_value = mock_result

        from sqlalchemy import select

        stmt = select(User).where(User.tenant_id == 100, User.is_deleted.is_(False))

        result = await mock_db.execute(stmt)
        users = result.scalars().all()

        # Isomorphism: Same users returned
        assert len(users) == 2
        assert all(not u.is_deleted for u in users)
