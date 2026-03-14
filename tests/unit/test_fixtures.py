"""Tests for test fixtures and module initialization.

This file contains:
- Unit tests: Sample data fixtures validation
- Unit tests: Module lazy-loading (__getattr__) validation
"""

import importlib
import pkgutil

import pytest

import src.app.core.tenancy
import src.app.modules
from src.app.modules.users.schemas import UserRead
from tests.conftest import MockUser


# ============================================================================
# FIXTURE TESTS
# ============================================================================


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


# ============================================================================
# MODULE INIT TESTS
# ============================================================================


def test_modules_init_getattr() -> None:
    """Dynamically test __getattr__ in all src.app.modules.*.__init__.py files.

    Many modules use a lazy-loading pattern with __getattr__ and __all__.
    This test ensures all defined exports can be properly imported, which
    covers the __getattr__ branches.
    """
    for _, modname, ispkg in pkgutil.iter_modules(src.app.modules.__path__):
        if ispkg:
            full_modname = f"src.app.modules.{modname}"
            module = importlib.import_module(full_modname)

            # If module uses lazy loading via __all__
            if hasattr(module, "__all__"):
                for attr_name in getattr(module, "__all__"):
                    # This triggers __getattr__
                    attr = getattr(module, attr_name)
                    assert attr is not None

                # Test invalid attribute raises AttributeError
                with pytest.raises(AttributeError):
                    getattr(module, "NonExistentAttributeForTesting123")


def test_core_tenancy_init() -> None:
    """Test src.app.core.tenancy.__init__.py lazy loading."""
    module = src.app.core.tenancy
    if hasattr(module, "__all__"):
        for attr_name in getattr(module, "__all__"):
            attr = getattr(module, attr_name)
            assert attr is not None

        with pytest.raises(AttributeError):
            getattr(module, "NonExistentAttributeForTesting123")
