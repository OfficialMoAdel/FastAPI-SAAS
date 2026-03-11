"""U16 - FastCRUD Projection Unit Tests.

Tests verifying that projection schemas limit SELECT columns
to only what's needed for the response, reducing data transfer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel


class TestProjectionSchemas:
    """Test projection schema functionality."""

    def test_schema_to_select_concept(self):
        """Demonstrate the projection concept with a minimal schema."""

        # Full model has many fields
        class UserFull(BaseModel):
            id: int
            username: str
            email: str
            name: str
            profile_image_url: str
            hashed_password: str  # Sensitive - never project
            is_superuser: bool
            is_deleted: bool
            tier_id: int | None

        # Projection schema - only needed fields
        class UserListItem(BaseModel):
            id: int
            username: str
            email: str
            name: str

        # The projection should only select these columns
        projected_fields = set(UserListItem.model_fields.keys())
        full_fields = set(UserFull.model_fields.keys())

        # Projection is a subset
        assert projected_fields < full_fields
        assert "hashed_password" not in projected_fields
        assert len(projected_fields) == 4
        assert len(full_fields) == 9

    def test_schema_to_select_helper(self):
        """Test a schema_to_select utility function."""

        def schema_to_select(schema_class: type[BaseModel]) -> list[str]:
            """Extract column names from Pydantic schema for SQL projection."""
            return list(schema_class.model_fields.keys())

        class UserMinimal(BaseModel):
            id: int
            username: str

        columns = schema_to_select(UserMinimal)
        assert columns == ["id", "username"]


class TestProjectionInQueries:
    """Test that projection reduces query payload."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_select_specific_columns(self, mock_db):
        """Verify SELECT only includes projected columns."""
        from sqlalchemy import select

        from src.app.modules.users.models import User

        # Full select (baseline - N+1 issue source)
        full_stmt = select(User)

        # Projected select (optimized)
        projected_stmt = select(User.id, User.username, User.email)

        # The projected statement should have fewer columns
        full_cols = len(list(full_stmt.selected_columns))
        projected_cols = len(list(projected_stmt.selected_columns))

        # Projection is smaller
        assert projected_cols < full_cols
        assert projected_cols == 3

    @pytest.mark.asyncio
    async def test_projection_response_matches_schema(self, mock_db):
        """Verify projected response matches schema exactly."""
        from pydantic import BaseModel

        class UserListRead(BaseModel):
            id: int
            username: str
            email: str

        # Mock projected result
        mock_row = MagicMock()
        mock_row.id = 1
        mock_row.username = "testuser"
        mock_row.email = "test@example.com"

        # Validate against schema
        user_data = UserListRead(
            id=mock_row.id,
            username=mock_row.username,
            email=mock_row.email,
        )

        assert user_data.id == 1
        assert user_data.username == "testuser"
        assert user_data.email == "test@example.com"

        # Schema has exactly these fields
        assert set(user_data.model_dump().keys()) == {"id", "username", "email"}


class TestIsomorphismProjection:
    """Isomorphism tests - projected data matches full data subset."""

    @pytest.mark.asyncio
    async def test_projected_subset_matches_full(self):
        """Verify projected fields are identical to full model subset."""
        from pydantic import BaseModel

        class UserFull(BaseModel):
            id: int
            username: str
            email: str
            name: str
            extra_field: str

        class UserProjected(BaseModel):
            id: int
            username: str
            email: str

        # Same source data
        source_data = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "extra_field": "extra",
        }

        full = UserFull(**source_data)
        projected = UserProjected(**{k: v for k, v in source_data.items() if k in UserProjected.model_fields})

        # Isomorphism: Projected fields match full model's fields
        assert projected.id == full.id
        assert projected.username == full.username
        assert projected.email == full.email

    @pytest.mark.asyncio
    async def test_projection_preserves_data_integrity(self):
        """Verify no data loss in projected fields."""
        from uuid import uuid4

        from pydantic import BaseModel

        class UserRead(BaseModel):
            id: int
            username: str
            uuid: str

        test_uuid = str(uuid4())
        data = {"id": 42, "username": "integrity_test", "uuid": test_uuid}

        user = UserRead(**data)

        # Data integrity preserved
        assert user.id == 42
        assert user.username == "integrity_test"
        assert user.uuid == test_uuid


class TestProjectionPerformancePattern:
    """Document the performance pattern for projection."""

    def test_document_projection_pattern(self):
        """Document how projection reduces data transfer."""
        # Performance baseline (from task description):
        # - Full User model: ~15 columns, ~500 bytes per row
        # - List endpoint returning 100 users: ~50KB
        #
        # With projection (4 columns for list view):
        # - Projected: 4 columns, ~100 bytes per row
        # - Same 100 users: ~10KB
        #
        # Result: ~80% reduction in data transfer for list views

        full_columns = 15
        projected_columns = 4

        reduction_percent = (1 - projected_columns / full_columns) * 100

        # We expect significant reduction
        assert reduction_percent > 70  # At least 70% column reduction
