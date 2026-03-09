"""Tests for test fixtures."""

from src.app.modules.users.schemas import UserRead
from tests.conftest import MockUser


class TestSampleDataFixtures:
    """Tests for sample data fixtures."""

    def test_sample_user_read(self, sample_user_read):
        """Test that sample_user_read fixture returns valid UserRead schema."""
        assert sample_user_read is not None
        assert isinstance(sample_user_read, UserRead)
        assert sample_user_read.id == 1
        assert sample_user_read.username is not None
        assert sample_user_read.email is not None
        assert sample_user_read.tenant_id == MockUser.DEFAULT_TENANT_ID

    def test_sample_user_read_has_required_fields(self, sample_user_read):
        """Test that sample_user_read has all required fields."""
        # These fields must not be None (required fields in UserRead)
        required_non_null_fields = [
            "id",
            "username",
            "email",
            "tenant_id",
        ]

        for field in required_non_null_fields:
            assert hasattr(sample_user_read, field), f"Missing field: {field}"
            assert getattr(sample_user_read, field) is not None, f"Field is None: {field}"

        # These fields can be None (optional fields)
        optional_fields = [
            "name",
            "tier_id",
            "profile_image_url",
        ]

        for field in optional_fields:
            assert hasattr(sample_user_read, field), f"Missing field: {field}"

    def test_sample_user_read_tenant_id_is_uuid(self, sample_user_read):
        """Test that tenant_id is a valid UUID."""
        from uuid import UUID

        assert isinstance(sample_user_read.tenant_id, UUID)
